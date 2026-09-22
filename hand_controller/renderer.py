from __future__ import annotations
import cv2
import numpy as np
from .models import HandTrack, HandDetection, Gesture, GesturePhase, RenderSnapshot, RenderDetection, RenderTrack
from .actions import ActionDispatcher


class Renderer:
    def __init__(self, debug: bool = True) -> None:
        self._debug = debug

    def toggle_debug(self) -> bool:
        self._debug = not self._debug
        return self._debug

    def draw_frame(
        self,
        frame: np.ndarray,
        tracks: list[HandTrack],
        detection_track_map: dict[int, int],
        detections: list[HandDetection],
        dispatcher: ActionDispatcher,
        now: float,
        fps: float = 30.0,
    ) -> None:
        height, width = frame.shape[:2]
        if self._debug:
            for det_idx, detection in enumerate(detections):
                track_id = detection_track_map.get(det_idx)
                if track_id is None:
                    continue
                track = tracks[track_id]
                self._draw_detection(frame, detection, track, track_id, width, height)
            self._draw_hud(frame, len(detections), dispatcher, now, width, height, fps)
        else:
            self._draw_clean_view(frame, len(detections), dispatcher, now, width, height, fps)

    def draw_snapshot(
        self,
        frame: np.ndarray,
        snapshot: RenderSnapshot,
        now: float,
        fps: float = 30.0,
    ) -> None:
        height, width = frame.shape[:2]
        if self._debug:
            for det_idx, det in enumerate(snapshot.detections):
                track_id = snapshot.detection_track_map.get(det_idx)
                if track_id is None or track_id >= len(snapshot.tracks):
                    continue
                track = snapshot.tracks[track_id]
                self._draw_snapshot_detection(frame, det, track, track_id, width, height)
            self._draw_snapshot_hud(frame, snapshot, now, width, height, fps)
        else:
            self._draw_snapshot_clean_view(frame, snapshot, now, width, height, fps)

    def _draw_snapshot_detection(
        self,
        frame: np.ndarray,
        det: RenderDetection,
        track: RenderTrack,
        track_id: int,
        width: int,
        height: int,
    ) -> None:
        for x, y in det.landmarks_xy:
            cx, cy = int(x * width), int(y * height)
            cv2.circle(frame, (cx, cy), 3, (0, 255, 0), -1)

        wrist_x = int(det.wrist_x * width)
        wrist_y = int(det.wrist_y * height)
        cv2.circle(frame, (wrist_x, wrist_y), 9, (255, 0, 0), -1)

        tx = max(5, min(wrist_x + 12, width - 300))
        ty = max(105, wrist_y - 100)

        state_text, state_color = self._phase_label_from_phase(track.phase, track.candidate)
        cv2.putText(
            frame, f"TRACK {track_id + 1} {state_text}",
            (tx, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.46, state_color, 2,
        )
        cv2.putText(
            frame, f"Gesture:{det.gesture.name}",
            (tx, ty + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.47, (255, 255, 255), 1,
        )
        speed_color = (0, 255, 0) if track.wrist_velocity <= 1.20 else (0, 165, 255)
        speed_text = "STEADY" if track.wrist_velocity <= 1.20 else "MOVING"
        cv2.putText(
            frame, f"Speed:{track.wrist_velocity:.2f} [{speed_text}]",
            (tx, ty + 44), cv2.FONT_HERSHEY_SIMPLEX, 0.36, speed_color, 1,
        )

    def _draw_snapshot_hud(
        self,
        frame: np.ndarray,
        snapshot: RenderSnapshot,
        now: float,
        width: int,
        height: int,
        fps: float,
    ) -> None:
        cv2.putText(
            frame, "SCISSORS = LEFT (Previous)",
            (20, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 0), 2,
        )
        cv2.putText(
            frame, "VERTICAL LIKE = RIGHT (Next)",
            (20, 58), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 2,
        )
        cv2.putText(
            frame, "OPEN PALM = B (Blackout)",
            (20, 84), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 160, 255), 2,
        )

        cv2.putText(
            frame, f"FPS: {fps:.1f}",
            (width - 130, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (0, 255, 0), 2,
        )
        if snapshot.result_age_ms > 0:
            cv2.putText(
                frame, f"Age: {snapshot.result_age_ms:.1f}ms",
                (width - 155, 58), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 200, 200), 1,
            )

        if snapshot.last_action_text and now - snapshot.last_action_time < 1.0:
            text = snapshot.last_action_text
            box_w = max(380, len(text) * 16 + 40)
            x1, y1 = 20, 110
            x2, y2 = 20 + box_w, 165
            roi = frame[y1:y2, x1:x2]
            overlay = roi.copy()
            cv2.rectangle(overlay, (0, 0), (x2 - x1, y2 - y1), (0, 140, 0), -1)
            cv2.addWeighted(overlay, 0.65, roi, 0.35, 0, roi)
            cv2.putText(
                frame, text,
                (35, 148), cv2.FONT_HERSHEY_SIMPLEX, 0.72, (255, 255, 255), 2,
            )

        cv2.putText(
            frame, f"Hands: {len(snapshot.detections)}/2",
            (10, height - 35), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (200, 200, 200), 1,
        )
        cv2.putText(
            frame, "[D] Toggle Debug | [H] Minimize | [Q] Quit",
            (10, height - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.39, (180, 180, 180), 1,
        )

    def _draw_snapshot_clean_view(
        self,
        frame: np.ndarray,
        snapshot: RenderSnapshot,
        now: float,
        width: int,
        height: int,
        fps: float,
    ) -> None:
        if snapshot.last_action_text and now - snapshot.last_action_time < 1.0:
            text = snapshot.last_action_text
            x1, y1 = 20, 20
            x2, y2 = min(480, width - 20), 80
            roi = frame[y1:y2, x1:x2]
            overlay = roi.copy()
            cv2.rectangle(overlay, (0, 0), (x2 - x1, y2 - y1), (0, 140, 0), -1)
            cv2.addWeighted(overlay, 0.65, roi, 0.35, 0, roi)
            cv2.putText(
                frame, text,
                (35, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.72, (255, 255, 255), 2,
            )

        cv2.putText(
            frame, f"FPS: {fps:.1f} | Hands: {len(snapshot.detections)}/2 [CLEAN MODE]",
            (10, height - 35), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (200, 200, 200), 1,
        )
        cv2.putText(
            frame, "[D] Toggle Debug | [H] Minimize to Taskbar | [Q] Quit",
            (10, height - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.39, (180, 180, 180), 1,
        )

    @classmethod
    def _phase_label_from_phase(cls, phase: GesturePhase, candidate: Gesture) -> tuple[str, tuple[int, int, int]]:
        if phase == GesturePhase.LATCHED:
            return "LATCHED", (0, 165, 255)
        if phase == GesturePhase.ARMED:
            return cls._ARMED_LABELS.get(candidate, ("ARMED", (0, 255, 0)))
        return "READY", (0, 255, 0)

    def _draw_detection(
        self,
        frame: np.ndarray,
        detection: HandDetection,
        track: HandTrack,
        track_id: int,
        width: int,
        height: int,
    ) -> None:
        landmarks = detection.landmarks
        gesture = detection.gesture_result.gesture

        for lm in landmarks:
            cx, cy = int(lm.x * width), int(lm.y * height)
            cv2.circle(frame, (cx, cy), 3, (0, 255, 0), -1)

        wrist_x = int(landmarks[0].x * width)
        wrist_y = int(landmarks[0].y * height)
        cv2.circle(frame, (wrist_x, wrist_y), 9, (255, 0, 0), -1)

        tip_x, tip_y, tip_color = self._gesture_tip(detection, width, height)
        if tip_x is not None:
            cv2.circle(frame, (tip_x, tip_y), 10, tip_color, -1)
            cv2.line(frame, (wrist_x, wrist_y), (tip_x, tip_y), tip_color, 2)

        tx = max(5, min(wrist_x + 12, width - 300))
        ty = max(105, wrist_y - 100)

        state_text, state_color = self._phase_label(track)
        cv2.putText(
            frame, f"TRACK {track_id + 1} {state_text}",
            (tx, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.46, state_color, 2,
        )
        cv2.putText(
            frame, f"Gesture:{gesture.name}",
            (tx, ty + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.47, (255, 255, 255), 1,
        )

        s = detection.gesture_result.scissors
        cv2.putText(
            frame,
            f"SCISSORS I:{'Y' if s.index_extended else 'N'} "
            f"M:{'Y' if s.middle_extended else 'N'} "
            f"V:{s.tip_separation:.2f}",
            (tx, ty + 43), cv2.FONT_HERSHEY_SIMPLEX, 0.36, (255, 255, 255), 1,
        )

        lk = detection.gesture_result.like
        cv2.putText(
            frame,
            f"LIKE Up:{'YES' if lk.vertical else 'NO'} Fold:{lk.folded_count}/4",
            (tx, ty + 63), cv2.FONT_HERSHEY_SIMPLEX, 0.36, (255, 255, 255), 1,
        )

        cv2.putText(
            frame, f"Raw:{detection.raw_label} {detection.raw_score:.2f}",
            (tx, ty + 83), cv2.FONT_HERSHEY_SIMPLEX, 0.36, (180, 180, 180), 1,
        )

        speed_color = (0, 255, 0) if track.wrist_velocity <= 1.20 else (0, 165, 255)
        speed_text = "STEADY" if track.wrist_velocity <= 1.20 else "MOVING"
        cv2.putText(
            frame, f"Speed:{track.wrist_velocity:.2f} [{speed_text}]",
            (tx, ty + 103), cv2.FONT_HERSHEY_SIMPLEX, 0.36, speed_color, 1,
        )

        op = detection.gesture_result.open_palm
        cv2.putText(
            frame,
            f"PALM I:{'Y' if op.index_extended else 'N'}"
            f" M:{'Y' if op.middle_extended else 'N'}"
            f" R:{'Y' if op.ring_extended else 'N'}"
            f" P:{'Y' if op.pinky_extended else 'N'}"
            f" T:{'Y' if op.thumb_extended else 'N'}",
            (tx, ty + 123), cv2.FONT_HERSHEY_SIMPLEX, 0.36, (200, 160, 255), 1,
        )


    def _draw_clean_view(
        self,
        frame: np.ndarray,
        num_detections: int,
        dispatcher: ActionDispatcher,
        now: float,
        width: int,
        height: int,
        fps: float,
    ) -> None:
        if now - dispatcher.last_action_time < 1.0:
            text = dispatcher.last_action_text
            x1, y1 = 20, 20
            x2, y2 = min(480, width - 20), 80
            roi = frame[y1:y2, x1:x2]
            overlay = roi.copy()
            cv2.rectangle(overlay, (0, 0), (x2 - x1, y2 - y1), (0, 140, 0), -1)
            cv2.addWeighted(overlay, 0.65, roi, 0.35, 0, roi)
            cv2.putText(
                frame, text,
                (35, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.72, (255, 255, 255), 2,
            )


        cv2.putText(
            frame, f"FPS: {fps:.1f} | Hands: {num_detections}/2 [CLEAN MODE]",
            (10, height - 35), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (200, 200, 200), 1,
        )
        cv2.putText(
            frame, "[D] Toggle Debug | [H] Minimize to Taskbar | [Q] Quit",
            (10, height - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.39, (180, 180, 180), 1,
        )

    def _draw_hud(
        self,
        frame: np.ndarray,
        num_detections: int,
        dispatcher: ActionDispatcher,
        now: float,
        width: int,
        height: int,
        fps: float,
    ) -> None:
        cv2.putText(
            frame, "SCISSORS = LEFT (Previous)",
            (20, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 0), 2,
        )
        cv2.putText(
            frame, "VERTICAL LIKE = RIGHT (Next)",
            (20, 58), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 2,
        )
        cv2.putText(
            frame, "OPEN PALM = B (Blackout)",
            (20, 84), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 160, 255), 2,
        )

        cv2.putText(
            frame, f"FPS: {fps:.1f}",
            (width - 130, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (0, 255, 0), 2,
        )

        if now - dispatcher.last_action_time < 1.0:
            cv2.putText(
                frame, dispatcher.last_action_text,
                (20, 112), cv2.FONT_HERSHEY_SIMPLEX, 0.67, (0, 255, 0), 2,
            )

        cv2.putText(
            frame, f"Hands detected: {num_detections} / 2",
            (10, height - 55), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1,
        )
        cv2.putText(
            frame, "Each hand is tracked independently",
            (10, height - 35), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1,
        )
        cv2.putText(
            frame, "SCISSORS=Prev | LIKE=Next | PALM=Blackout | D=Debug | H=Min | Q=Quit",
            (10, height - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.39, (255, 255, 255), 1,
        )


    @staticmethod
    def _gesture_tip(
        detection: HandDetection,
        width: int,
        height: int,
    ) -> tuple[int | None, int | None, tuple[int, int, int]]:
        gesture = detection.gesture_result.gesture
        landmarks = detection.landmarks
        if gesture == Gesture.SCISSORS:
            tip_x = int((landmarks[8].x + landmarks[12].x) / 2 * width)
            tip_y = int((landmarks[8].y + landmarks[12].y) / 2 * height)
            return tip_x, tip_y, (255, 255, 0)
        if gesture == Gesture.LIKE:
            tip_x = int(landmarks[4].x * width)
            tip_y = int(landmarks[4].y * height)
            return tip_x, tip_y, (0, 255, 255)
        if gesture == Gesture.OPEN_PALM:
            # Show center of all fingertips (4, 8, 12, 16, 20)
            tips = [4, 8, 12, 16, 20]
            tip_x = int(sum(landmarks[i].x for i in tips) / len(tips) * width)
            tip_y = int(sum(landmarks[i].y for i in tips) / len(tips) * height)
            return tip_x, tip_y, (200, 160, 255)
        return None, None, (255, 255, 255)


    _ARMED_LABELS: dict[Gesture, tuple[str, tuple[int, int, int]]] = {
        Gesture.SCISSORS: ("SCISSORS ARMED", (255, 255, 0)),
        Gesture.LIKE: ("LIKE ARMED", (0, 255, 255)),
        Gesture.OPEN_PALM: ("PALM ARMED", (200, 160, 255)),
    }

    @classmethod
    def _phase_label(cls, track: HandTrack) -> tuple[str, tuple[int, int, int]]:
        if track.phase == GesturePhase.LATCHED:
            return "LATCHED", (0, 165, 255)
        if track.phase == GesturePhase.ARMED:
            return cls._ARMED_LABELS.get(track.candidate, ("ARMED", (0, 255, 0)))
        return "READY", (0, 255, 0)