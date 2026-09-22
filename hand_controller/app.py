from __future__ import annotations
import ctypes
import cv2
import mediapipe as mp
import numpy as np
import sys
import threading
import time
from collections import deque
from typing import Any, NamedTuple


from .config import AppConfig
from .models import (
    HandDetection,
    SlideAction,
    Gesture,
    GesturePhase,
    RenderSnapshot,
    RenderDetection,
    RenderTrack,
)
from .gestures import GestureRecognizer
from .tracker import HandTracker
from .state_machine import GestureStateMachine
from .actions import ActionDispatcher
from .renderer import Renderer
from .camera import CameraSource, CameraFrame, create_camera_source


_BaseOptions = mp.tasks.BaseOptions
_HandLandmarker = mp.tasks.vision.HandLandmarker
_HandLandmarkerOptions = mp.tasks.vision.HandLandmarkerOptions
_RunningMode = mp.tasks.vision.RunningMode

WIN_NAME = "Gesture Slide Controller"

_user32 = ctypes.windll.user32


class _FrameMeta(NamedTuple):
    capture_time: float
    submit_time: float


class _ResultPacket(NamedTuple):
    result: Any
    timestamp_ms: int
    capture_time: float
    submit_time: float
    callback_time: float


class _AsyncSharedState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.new_result_event = threading.Event()
        self.stop_event = threading.Event()
        self.latest_packet: _ResultPacket | None = None
        self.pending_meta: dict[int, _FrameMeta] = {}
        self.callback_count: int = 0
        self.callback_error_count: int = 0
        self.mediapipe_input_drop: int = 0
        self.control_overwrite_drop: int = 0
        self.metadata_eviction: int = 0
        self.is_closed: bool = False


class App:
    def __init__(self, cfg: AppConfig | None = None) -> None:
        self._cfg = cfg or AppConfig()
        self._recognizer = GestureRecognizer(self._cfg.gesture)
        self._tracker = HandTracker(self._cfg.tracking)
        self._state_machine = GestureStateMachine(self._cfg.tracking)
        self._dispatcher = ActionDispatcher(
            cooldown=self._cfg.global_action_cooldown,
            audio_feedback=self._cfg.audio_feedback,
        )
        self._renderer = Renderer(self._cfg.debug)
        self._shared = _AsyncSharedState()
        self._render_lock = threading.Lock()
        self._latest_render_snapshot: RenderSnapshot = RenderSnapshot()
        self._control_thread: threading.Thread | None = None

    def _on_async_result(self, result, output_image, timestamp_ms: int) -> None:
        try:
            now_perf = time.perf_counter()
            with self._shared.lock:
                if self._shared.is_closed or self._shared.stop_event.is_set():
                    return

                # Prune older pending frames as mediapipe_input_drop
                older_keys = [k for k in self._shared.pending_meta if k < timestamp_ms]
                for k in older_keys:
                    del self._shared.pending_meta[k]
                    self._shared.mediapipe_input_drop += 1

                meta = self._shared.pending_meta.pop(timestamp_ms, None)
                cap_time = meta.capture_time if meta else now_perf
                sub_time = meta.submit_time if meta else now_perf

                packet = _ResultPacket(
                    result=result,
                    timestamp_ms=timestamp_ms,
                    capture_time=cap_time,
                    submit_time=sub_time,
                    callback_time=now_perf,
                )

                # Overwrite tracking: an unconsumed packet was superseded by a newer one
                if self._shared.latest_packet is not None:
                    self._shared.control_overwrite_drop += 1

                self._shared.latest_packet = packet
                self._shared.callback_count += 1

            # Signal control worker thread
            self._shared.new_result_event.set()
        except Exception as e:
            with self._shared.lock:
                self._shared.callback_error_count += 1
            if sys.stderr is not None:
                try:
                    sys.stderr.write(f"Callback error: {e}\n")
                except Exception:
                    pass

    def _control_worker_loop(self) -> None:
        last_processed_ms = -1

        while not self._shared.stop_event.is_set():
            signaled = self._shared.new_result_event.wait(timeout=0.05)
            if self._shared.stop_event.is_set():
                break
            if not signaled:
                continue

            packet: _ResultPacket | None = None
            with self._shared.lock:
                if (
                    self._shared.latest_packet is not None
                    and self._shared.latest_packet.timestamp_ms > last_processed_ms
                ):
                    packet = self._shared.latest_packet
                    self._shared.latest_packet = None
                self._shared.new_result_event.clear()

            if packet is None:
                continue

            last_processed_ms = packet.timestamp_ms
            event_time = packet.capture_time

            # HandTracker & GestureStateMachine mutation is strictly owned by this thread
            detections = self._build_detections(packet.result)
            assignments = self._tracker.assign(detections, event_time)
            detection_track_map: dict[int, int] = {}

            for det_idx, track_id in assignments.items():
                detection = detections[det_idx]
                track = self._tracker.tracks[track_id]
                self._tracker.update_position(track, detection, event_time)
                detection_track_map[det_idx] = track_id

                action = self._state_machine.update(track, detection, event_time)
                if action != SlideAction.NONE:
                    dispatched = self._dispatcher.dispatch(action, event_time)
                    if dispatched:
                        self._state_machine.latch(track)

            self._tracker.expire_lost_tracks(assignments.values(), event_time)

            # Publish truly immutable RenderSnapshot for UI Main Thread
            self._publish_render_snapshot(detections, detection_track_map, packet)

    def _publish_render_snapshot(
        self,
        detections: list[HandDetection],
        detection_track_map: dict[int, int],
        packet: _ResultPacket,
    ) -> None:
        render_dets = []
        for d in detections:
            pts = tuple((lm.x, lm.y) for lm in d.landmarks)
            render_dets.append(RenderDetection(
                landmarks_xy=pts,
                wrist_x=d.wrist_x,
                wrist_y=d.wrist_y,
                gesture=d.gesture_result.gesture,
                raw_label=d.raw_label,
                raw_score=d.raw_score,
            ))

        render_trks = []
        for t in self._tracker.tracks:
            render_trks.append(RenderTrack(
                track_id=t.track_id,
                wrist_pos=t.wrist_pos,
                phase=t.phase,
                gesture=t.candidate if t.phase == GesturePhase.ARMED else Gesture.NONE,
                candidate=t.candidate,
                wrist_velocity=t.wrist_velocity,
                raw_label="Hand",
            ))

        now_perf = time.perf_counter()
        age_ms = (now_perf - packet.capture_time) * 1000.0

        snapshot = RenderSnapshot(
            tracks=tuple(render_trks),
            detections=tuple(render_dets),
            detection_track_map=dict(detection_track_map),
            last_action_text=self._dispatcher.last_action_text,
            last_action_time=self._dispatcher.last_action_time,
            result_age_ms=age_ms,
            timestamp=now_perf,
        )

        with self._render_lock:
            self._latest_render_snapshot = snapshot

    def run(self) -> None:
        camera = self._setup_camera()

        options = self._build_landmarker_options()
        mode = self._cfg.running_mode.upper()
        is_worker = (mode == "LIVE_STREAM_WORKER")
        is_polling = (mode in ["LIVE_STREAM", "LIVE_STREAM_POLLING"])
        is_video = (mode == "VIDEO")

        hwnd = 0
        if self._cfg.preview_enabled:
            cv2.namedWindow(WIN_NAME, cv2.WINDOW_AUTOSIZE)
            hwnd = _user32.FindWindowW(None, WIN_NAME)

        # Start non-daemon Control Worker thread if in worker mode
        if is_worker:
            self._control_thread = threading.Thread(
                target=self._control_worker_loop,
                name="ControlWorkerThread",
                daemon=False,
            )
            self._control_thread.start()

        try:
            with _HandLandmarker.create_from_options(options) as landmarker:
                self._set_process_priority()
                last_timestamp_ms = -1
                last_processed_ms = -1
                fps_history: deque[float] = deque(maxlen=20)
                detections: list[HandDetection] = []
                detection_track_map: dict[int, int] = {}

                while True:
                    if self._cfg.preview_enabled:
                        if not hwnd:
                            hwnd = _user32.FindWindowW(None, WIN_NAME)
                        elif not _user32.IsWindow(hwnd):
                            break

                    success, cam_frame = camera.read_latest(timeout_sec=0.05)
                    if not success or cam_frame is None:
                        continue

                    capture_time = cam_frame.capture_time
                    now = time.time()
                    fps_history.append(now)
                    dt = fps_history[-1] - fps_history[0]
                    fps = (len(fps_history) - 1) / dt if len(fps_history) > 1 and dt > 0 else 30.0

                    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=cam_frame.image_rgb)

                    # Strictly monotonic timestamp enforcement
                    now_ms = time.monotonic_ns() // 1_000_000
                    timestamp_ms = max(now_ms, last_timestamp_ms + 1)
                    last_timestamp_ms = timestamp_ms

                    submit_time = time.perf_counter()

                    if is_worker:
                        # Event-driven Control Worker architecture
                        with self._shared.lock:
                            if len(self._shared.pending_meta) >= 120:
                                oldest_keys = sorted(self._shared.pending_meta.keys())[:30]
                                for k in oldest_keys:
                                    del self._shared.pending_meta[k]
                                    self._shared.metadata_eviction += 1
                            self._shared.pending_meta[timestamp_ms] = _FrameMeta(capture_time, submit_time)

                        landmarker.detect_async(mp_image, timestamp_ms)

                    elif is_polling:
                        # Polling architecture (historical comparison)
                        with self._shared.lock:
                            if len(self._shared.pending_meta) >= 120:
                                oldest_keys = sorted(self._shared.pending_meta.keys())[:30]
                                for k in oldest_keys:
                                    del self._shared.pending_meta[k]
                                    self._shared.metadata_eviction += 1
                            self._shared.pending_meta[timestamp_ms] = _FrameMeta(capture_time, submit_time)

                        landmarker.detect_async(mp_image, timestamp_ms)

                        packet = None
                        with self._shared.lock:
                            if self._shared.latest_packet is not None and self._shared.latest_packet.timestamp_ms > last_processed_ms:
                                packet = self._shared.latest_packet
                                self._shared.latest_packet = None

                        if packet is not None:
                            last_processed_ms = packet.timestamp_ms
                            event_time = packet.capture_time
                            detections = self._build_detections(packet.result)
                            assignments = self._tracker.assign(detections, event_time)
                            detection_track_map = {}

                            for det_idx, track_id in assignments.items():
                                detection = detections[det_idx]
                                track = self._tracker.tracks[track_id]
                                self._tracker.update_position(track, detection, event_time)
                                detection_track_map[det_idx] = track_id

                                action = self._state_machine.update(track, detection, event_time)
                                if action != SlideAction.NONE:
                                    dispatched = self._dispatcher.dispatch(action, event_time)
                                    if dispatched:
                                        self._state_machine.latch(track)

                            self._tracker.expire_lost_tracks(assignments.values(), event_time)

                    else:
                        # Synchronous VIDEO baseline
                        result = landmarker.detect_for_video(mp_image, timestamp_ms)
                        detections = self._build_detections(result)
                        assignments = self._tracker.assign(detections, now)
                        detection_track_map = {}

                        for det_idx, track_id in assignments.items():
                            detection = detections[det_idx]
                            track = self._tracker.tracks[track_id]
                            self._tracker.update_position(track, detection, now)
                            detection_track_map[det_idx] = track_id

                            action = self._state_machine.update(track, detection, now)
                            if action != SlideAction.NONE:
                                dispatched = self._dispatcher.dispatch(action, now)
                                if dispatched:
                                    self._state_machine.latch(track)

                        self._tracker.expire_lost_tracks(assignments.values(), now)

                    is_minimized = bool(_user32.IsIconic(hwnd)) if hwnd else False

                    if self._cfg.preview_enabled:
                        if not is_minimized:
                            display_frame = cv2.cvtColor(cam_frame.image_rgb, cv2.COLOR_RGB2BGR)
                            if is_worker:
                                with self._render_lock:
                                    snapshot = self._latest_render_snapshot
                                self._renderer.draw_snapshot(display_frame, snapshot, now, fps=fps)
                            else:
                                self._renderer.draw_frame(
                                    display_frame,
                                    self._tracker.tracks,
                                    detection_track_map,
                                    detections,
                                    self._dispatcher,
                                    now,
                                    fps=fps,
                                )
                            cv2.imshow(WIN_NAME, display_frame)

                        key = cv2.waitKey(1) & 0xFF
                        if key == ord("q"):
                            break
                        elif key == ord("d"):
                            self._renderer.toggle_debug()
                        elif key == ord("h"):
                            self._minimize_window(hwnd)

        finally:
            # Deterministic clean shutdown sequence
            with self._shared.lock:
                self._shared.is_closed = True
            self._shared.stop_event.set()
            self._shared.new_result_event.set()

            if is_worker and self._control_thread is not None:
                self._control_thread.join(timeout=3.0)
                if self._control_thread.is_alive():
                    print("CRITICAL: Control worker thread failed to terminate cleanly within timeout!", file=sys.stderr)

            camera.close()
            if self._cfg.preview_enabled:
                cv2.destroyAllWindows()

    @staticmethod
    def _minimize_window(hwnd: int) -> None:
        try:
            if hwnd:
                _user32.ShowWindow(hwnd, 6)
        except Exception:
            pass

    def _build_detections(self, result) -> list[HandDetection]:
        if not result or not result.hand_landmarks:
            return []

        detections: list[HandDetection] = []
        for i, landmarks in enumerate(result.hand_landmarks):
            raw_label = "Unknown"
            raw_score = 0.0
            if result.handedness and i < len(result.handedness) and result.handedness[i]:
                cat = result.handedness[i][0]
                raw_label = cat.category_name
                raw_score = cat.score

            gesture_result = self._recognizer.classify(landmarks)
            wrist = landmarks[0]
            detections.append(HandDetection(
                landmarks=landmarks,
                wrist_x=wrist.x,
                wrist_y=wrist.y,
                gesture_result=gesture_result,
                raw_label=raw_label,
                raw_score=raw_score,
            ))

        return detections

    def _setup_camera(self) -> CameraSource:
        return create_camera_source(
            self._cfg.camera,
            force_winrt_failure=self._cfg.camera.force_winrt_init_failure,
        )

    @staticmethod
    def _set_process_priority() -> None:
        """Sets Windows process priority to ABOVE_NORMAL safely without hard dependency."""
        try:
            import psutil
            proc = psutil.Process()
            proc.nice(psutil.ABOVE_NORMAL_PRIORITY_CLASS)
        except Exception:
            pass

    def _build_landmarker_options(self) -> _HandLandmarkerOptions:
        cfg = self._cfg
        mode = cfg.running_mode.upper()
        is_live = (mode in ["LIVE_STREAM", "LIVE_STREAM_POLLING", "LIVE_STREAM_WORKER"])
        running_mode = _RunningMode.LIVE_STREAM if is_live else _RunningMode.VIDEO
        callback = self._on_async_result if is_live else None

        return _HandLandmarkerOptions(
            base_options=_BaseOptions(model_asset_path=cfg.model_path),
            running_mode=running_mode,
            num_hands=cfg.num_hands,
            min_hand_detection_confidence=cfg.min_hand_detection_confidence,
            min_hand_presence_confidence=cfg.min_hand_presence_confidence,
            min_tracking_confidence=cfg.min_tracking_confidence,
            result_callback=callback,
        )