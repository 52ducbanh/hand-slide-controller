"""
FINAL RELEASE VALIDATION — 10-Minute Continuous Live Stream Stability
Runs 600 seconds on physical webcam using the exact production stack:
- WinRT CameraSource (1280x720 @ 30 FPS, NV12)
- LIVE_STREAM_WORKER mode with MediaPipe HandLandmarker (num_hands=2)
- Current native Win32 SendInput ActionDispatcher
- Current standard opencv-python build
Outputs:
- FINAL_STABILITY_600S.json
"""

from __future__ import annotations
import json
import os
import sys
import threading
import time
from typing import Any, NamedTuple

import cv2
import mediapipe as mp
import numpy as np
import psutil

from hand_controller.config import AppConfig, CameraConfig
from hand_controller.camera import WinRTCameraSource, CameraFrame
from hand_controller.gestures import GestureRecognizer
from hand_controller.tracker import HandTracker
from hand_controller.state_machine import GestureStateMachine
from hand_controller.actions import ActionDispatcher
from hand_controller.models import SlideAction, HandDetection

_BaseOptions = mp.tasks.BaseOptions
_HandLandmarker = mp.tasks.vision.HandLandmarker
_HandLandmarkerOptions = mp.tasks.vision.HandLandmarkerOptions
_RunningMode = mp.tasks.vision.RunningMode


class _FrameRecord(NamedTuple):
    frame_idx: int
    capture_time: float
    submit_time: float
    timestamp_ms: int


class _WorkerPacket(NamedTuple):
    result: Any
    timestamp_ms: int
    frame_idx: int
    capture_time: float
    submit_time: float
    callback_time: float


def run_10min_validation(duration_sec: float = 600.0, sample_interval_sec: float = 5.0) -> dict[str, Any]:
    print("==================================================================")
    print(f"STARTING FINAL RELEASE 10-MINUTE (600s) LIVE STABILITY VALIDATION")
    print(f"Hardware: Physical Webcam 0 | WinRT MediaFrameReader Realtime")
    print(f"Config: 1280x720 @ 30 FPS | num_hands=2 | Native Win32 SendInput")
    print("==================================================================")

    proc = psutil.Process()
    cfg = AppConfig(
        running_mode="LIVE_STREAM_WORKER",
        num_hands=2,
        min_hand_detection_confidence=0.25,
        min_hand_presence_confidence=0.25,
        min_tracking_confidence=0.25,
        camera=CameraConfig(index=0, width=1280, height=720, fps=30, backend="WINRT")
    )

    cam_cfg = CameraConfig(index=0, width=1280, height=720, fps=30, backend="WINRT")
    cam = WinRTCameraSource(cam_cfg)
    if not cam.open():
        raise RuntimeError("Failed to open WinRT camera for 10-minute stability test")

    recognizer = GestureRecognizer(cfg.gesture)
    tracker = HandTracker(cfg.tracking)
    state_machine = GestureStateMachine(cfg.tracking)
    dispatcher = ActionDispatcher(cooldown=cfg.global_action_cooldown)

    # Counters
    counter_lock = threading.Lock()
    pending_meta: dict[int, _FrameRecord] = {}
    
    callbacks_received = 0
    worker_packets_consumed = 0
    results_processed = 0
    mediapipe_input_drops = 0
    control_overwrite_drops = 0
    actions_dispatched = 0
    exceptions_caught = 0

    worker_event = threading.Event()
    worker_stop_event = threading.Event()
    latest_worker_packet: _WorkerPacket | None = None

    def on_async_result(result: Any, output_image: Any, timestamp_ms: int) -> None:
        nonlocal callbacks_received, mediapipe_input_drops, control_overwrite_drops, latest_worker_packet
        try:
            now_perf = time.perf_counter()
            with counter_lock:
                older_keys = [k for k in pending_meta if k < timestamp_ms]
                for k in older_keys:
                    del pending_meta[k]
                    mediapipe_input_drops += 1

                meta = pending_meta.pop(timestamp_ms, None)
                frame_idx = meta.frame_idx if meta else -1
                cap_time = meta.capture_time if meta else now_perf
                sub_time = meta.submit_time if meta else now_perf

                packet = _WorkerPacket(
                    result=result,
                    timestamp_ms=timestamp_ms,
                    frame_idx=frame_idx,
                    capture_time=cap_time,
                    submit_time=sub_time,
                    callback_time=now_perf,
                )

                if latest_worker_packet is not None:
                    control_overwrite_drops += 1

                latest_worker_packet = packet
                callbacks_received += 1

            worker_event.set()
        except Exception as e:
            nonlocal exceptions_caught
            exceptions_caught += 1
            print(f"Callback exception: {e}", file=sys.stderr)

    def worker_loop() -> None:
        nonlocal worker_packets_consumed, results_processed, actions_dispatched, latest_worker_packet
        last_processed_ms = -1

        while not worker_stop_event.is_set():
            if not worker_event.wait(timeout=0.05):
                continue
            if worker_stop_event.is_set():
                break

            pkt: _WorkerPacket | None = None
            with counter_lock:
                if latest_worker_packet is not None and latest_worker_packet.timestamp_ms > last_processed_ms:
                    pkt = latest_worker_packet
                    latest_worker_packet = None
                worker_event.clear()

            if pkt is None:
                continue

            last_processed_ms = pkt.timestamp_ms
            worker_packets_consumed += 1

            # Process detections & state machine
            res = pkt.result
            detections = []
            if res and res.hand_landmarks:
                for i, lm in enumerate(res.hand_landmarks):
                    raw_label = "Unknown"
                    raw_score = 0.0
                    if res.handedness and i < len(res.handedness) and res.handedness[i]:
                        raw_label = res.handedness[i][0].category_name
                        raw_score = res.handedness[i][0].score
                    g_res = recognizer.classify(lm)
                    detections.append(HandDetection(
                        landmarks=lm,
                        wrist_x=lm[0].x,
                        wrist_y=lm[0].y,
                        gesture_result=g_res,
                        raw_label=raw_label,
                        raw_score=raw_score,
                    ))

            event_time = pkt.capture_time
            assignments = tracker.assign(detections, event_time)
            for det_idx, track_id in assignments.items():
                det = detections[det_idx]
                trk = tracker.tracks[track_id]
                tracker.update_position(trk, det, event_time)
                act = state_machine.update(trk, det, event_time)
                if act != SlideAction.NONE:
                    if dispatcher.dispatch(act, event_time):
                        state_machine.latch(trk)
                        actions_dispatched += 1

            tracker.expire_lost_tracks(assignments.values(), event_time)
            results_processed += 1

    worker_thread = threading.Thread(target=worker_loop, name="ValidationWorker", daemon=False)
    worker_thread.start()

    options = _HandLandmarkerOptions(
        base_options=_BaseOptions(model_asset_path=cfg.model_path),
        running_mode=_RunningMode.LIVE_STREAM,
        num_hands=2,
        min_hand_detection_confidence=0.25,
        min_hand_presence_confidence=0.25,
        min_tracking_confidence=0.25,
        result_callback=on_async_result,
    )

    time_series: list[dict[str, Any]] = []
    frames_submitted = 0
    last_timestamp_ms = -1

    t_start = time.perf_counter()
    next_sample_time = t_start + sample_interval_sec

    # Main capture & submit loop
    with _HandLandmarker.create_from_options(options) as landmarker:
        frame_idx = 0
        while True:
            t_now = time.perf_counter()
            elapsed = t_now - t_start
            if elapsed >= duration_sec:
                break

            # Ingestion
            ok, frame = cam.read_latest(timeout_sec=0.05)
            if not ok or frame is None:
                continue

            frame_idx += 1
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame.image_rgb)
            now_ms = time.monotonic_ns() // 1_000_000
            ts_ms = max(now_ms, last_timestamp_ms + 1)
            last_timestamp_ms = ts_ms

            sub_time = time.perf_counter()
            with counter_lock:
                pending_meta[ts_ms] = _FrameRecord(frame_idx, frame.capture_time, sub_time, ts_ms)

            landmarker.detect_async(mp_image, ts_ms)
            frames_submitted += 1

            # Sample metrics
            if t_now >= next_sample_time:
                mem_info = proc.memory_info()
                rss_mb = round(mem_info.rss / (1024 * 1024), 2)
                cpu_pct = proc.cpu_percent(interval=None)

                time_series.append({
                    "elapsed_sec": round(elapsed, 1),
                    "rss_mb": rss_mb,
                    "cpu_pct": cpu_pct,
                    "frames_submitted": frames_submitted,
                    "callbacks_received": callbacks_received,
                    "results_processed": results_processed,
                })
                print(f"  [{round(elapsed, 0):>4.0f}s / {duration_sec:.0f}s] RSS: {rss_mb:.2f} MB | CPU: {cpu_pct:>4.1f}% | Sub: {frames_submitted} | Proc: {results_processed} | Drops: {mediapipe_input_drops}", flush=True)
                next_sample_time += sample_interval_sec

    total_elapsed = time.perf_counter() - t_start

    # Clean shutdown
    worker_stop_event.set()
    worker_event.set()
    worker_thread.join(timeout=3.0)
    worker_alive = worker_thread.is_alive()

    cam.close()

    # Query camera counters
    cam_stats = cam.stats

    # Memory Analysis
    # Exclude first 60 seconds
    warmup_cutoff = 60.0
    post_60_samples = [s for s in time_series if s["elapsed_sec"] >= warmup_cutoff]
    post_300_samples = [s for s in time_series if s["elapsed_sec"] >= 300.0]

    rss_60 = next((s["rss_mb"] for s in time_series if s["elapsed_sec"] >= 60.0), time_series[0]["rss_mb"] if time_series else 0.0)
    rss_300 = next((s["rss_mb"] for s in time_series if s["elapsed_sec"] >= 300.0), time_series[len(time_series)//2]["rss_mb"] if time_series else 0.0)
    rss_600 = time_series[-1]["rss_mb"] if time_series else 0.0

    steady_rss_vals = [s["rss_mb"] for s in post_60_samples]
    steady_min = min(steady_rss_vals) if steady_rss_vals else 0.0
    steady_max = max(steady_rss_vals) if steady_rss_vals else 0.0

    # Linear regression slopes
    def calc_slope_mb_per_min(samples):
        if len(samples) < 2:
            return 0.0
        x = np.array([s["elapsed_sec"] / 60.0 for s in samples])
        y = np.array([s["rss_mb"] for s in samples])
        slope, _ = np.polyfit(x, y, 1)
        return round(float(slope), 4)

    slope_60_to_600 = calc_slope_mb_per_min(post_60_samples)
    slope_300_to_600 = calc_slope_mb_per_min(post_300_samples)

    memory_verdict = (
        "MEMORY PLATEAU CONFIRMED\nNO EVIDENCE OF UNBOUNDED LEAK"
        if abs(slope_300_to_600) < 0.25 else "POSSIBLE LEAK"
    )

    out = {
        "validation_metadata": {
            "test_name": "FINAL_RELEASE_STABILITY_600S",
            "commit": "606ac85",
            "duration_sec": round(total_elapsed, 2),
            "camera_backend": "WINRT",
            "resolution": "1280x720",
            "target_fps": 30,
            "running_mode": "LIVE_STREAM_WORKER",
            "num_hands": 2,
            "action_dispatcher": "NATIVE_WIN32_SENDINPUT"
        },
        "raw_stage_counters": {
            "frame_arrived_events": cam_stats["frame_arrived_events"],
            "frames_acquired": cam_stats["frames_acquired"],
            "frames_rgb_ready": cam_stats["frames_rgb_ready"],
            "frames_published": cam_stats["frames_published"],
            "frames_consumed": cam_stats["frames_consumed_by_app"],
            "frames_submitted": frames_submitted,
            "callbacks_received": callbacks_received,
            "worker_packets_consumed": worker_packets_consumed,
            "results_processed": results_processed,
            "actions_dispatched": actions_dispatched,
            "mediapipe_drops": mediapipe_input_drops,
            "control_overwrites": control_overwrite_drops,
            "exceptions": exceptions_caught,
            "worker_thread_clean_join": not worker_alive
        },
        "throughput": {
            "source_fps": round(cam_stats["frames_acquired"] / total_elapsed, 2),
            "processed_fps": round(results_processed / total_elapsed, 2),
            "submitted_fps": round(frames_submitted / total_elapsed, 2),
        },
        "memory_profile": {
            "rss_at_60s_mb": rss_60,
            "rss_at_300s_mb": rss_300,
            "rss_at_600s_mb": rss_600,
            "steady_state_min_mb": steady_min,
            "steady_state_max_mb": steady_max,
            "steady_state_range_mb": round(steady_max - steady_min, 2),
            "slope_60_to_600_mb_per_min": slope_60_to_600,
            "slope_300_to_600_mb_per_min": slope_300_to_600,
            "memory_verdict": memory_verdict
        },
        "time_series_samples": time_series
    }

    print("\n==================================================================")
    print("FINAL RELEASE 10-MINUTE STABILITY RESULTS")
    print("==================================================================")
    print(f"Frames Acquired (Source):   {cam_stats['frames_acquired']} ({out['throughput']['source_fps']} FPS)")
    print(f"Frames Submitted:           {frames_submitted} ({out['throughput']['submitted_fps']} FPS)")
    print(f"Results Processed:          {results_processed} ({out['throughput']['processed_fps']} FPS)")
    print(f"MediaPipe Drops:            {mediapipe_input_drops}")
    print(f"Exceptions:                 {exceptions_caught}")
    print(f"Worker Clean Shutdown:      {not worker_alive}")
    print(f"RSS @ 60s:                  {rss_60:.2f} MB")
    print(f"RSS @ 300s:                 {rss_300:.2f} MB")
    print(f"RSS @ 600s:                 {rss_600:.2f} MB")
    print(f"Slope (300s -> 600s):       {slope_300_to_600:.4f} MB/min")
    print(f"Memory Verdict:\n{memory_verdict}")
    print("==================================================================")

    with open("FINAL_STABILITY_600S.json", "w") as f:
        json.dump(out, f, indent=2)
    print("Saved: FINAL_STABILITY_600S.json")
    return out

if __name__ == "__main__":
    dur = float(sys.argv[1]) if len(sys.argv) > 1 else 600.0
    run_10min_validation(duration_sec=dur)
