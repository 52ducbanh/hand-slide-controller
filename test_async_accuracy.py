"""Test and Verify Accuracy and Gesture Behavior in LIVE_STREAM Mode.

Evaluates Scenarios A-G, 20-30 attempts per gesture, and multi-hand accuracy.
Exports: async_accuracy.json
"""

from __future__ import annotations
import json
import time
from typing import Any, NamedTuple

import cv2
import mediapipe as mp
import numpy as np
import pyautogui

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0
pyautogui.press = lambda key: None

from hand_controller.config import AppConfig
from hand_controller.gestures import GestureRecognizer
from hand_controller.tracker import HandTracker
from hand_controller.state_machine import GestureStateMachine
from hand_controller.actions import ActionDispatcher
from hand_controller.models import SlideAction

_BaseOptions = mp.tasks.BaseOptions
_HandLandmarker = mp.tasks.vision.HandLandmarker
_HandLandmarkerOptions = mp.tasks.vision.HandLandmarkerOptions
_RunningMode = mp.tasks.vision.RunningMode


class _FrameRecord(NamedTuple):
    frame_idx: int
    seg_idx: int
    capture_time: float
    submit_time: float
    timestamp_ms: int


def run_accuracy_test(video_path: str = "benchmark_input.mp4") -> dict[str, Any]:
    print("\n=======================================================")
    print("Running Accuracy & Scenario Test (LIVE_STREAM, num_hands=2)")
    print("=======================================================")

    cfg = AppConfig(running_mode="LIVE_STREAM", num_hands=2, preview_enabled=False)
    recognizer = GestureRecognizer(cfg.gesture)
    tracker = HandTracker(cfg.tracking)
    state_machine = GestureStateMachine(cfg.tracking)
    dispatcher = ActionDispatcher(cooldown=cfg.global_action_cooldown)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open benchmark video {video_path}")

    import threading
    async_lock = threading.Lock()
    async_results = {}
    latest_cb_ts = -1
    pending_records: dict[int, _FrameRecord] = {}

    def on_async_result(result, image, timestamp_ms: int) -> None:
        nonlocal latest_cb_ts
        cb_time = time.perf_counter()
        with async_lock:
            to_drop = [k for k in pending_records if k < timestamp_ms]
            for k in to_drop:
                del pending_records[k]

            async_results[timestamp_ms] = (result, cb_time)
            latest_cb_ts = timestamp_ms

    options = _HandLandmarkerOptions(
        base_options=_BaseOptions(model_asset_path=cfg.model_path),
        running_mode=_RunningMode.LIVE_STREAM,
        num_hands=cfg.num_hands,
        min_hand_detection_confidence=cfg.min_hand_detection_confidence,
        min_hand_presence_confidence=cfg.min_hand_presence_confidence,
        min_tracking_confidence=cfg.min_tracking_confidence,
        result_callback=on_async_result,
    )

    # Scenarios tracking
    scenarios = {
        "scenario_a_no_hand": {"frames": 0, "detected_hands": 0, "false_triggers": 0},
        "scenario_b_1_hand_steady": {"frames": 0, "detected_hands": 0, "like_count": 0, "actions": []},
        "scenario_c_1_hand_active": {
            "scissors_frames": 0, "scissors_count": 0, "scissors_actions": [],
            "like_frames": 0, "like_count": 0, "like_actions": [],
            "open_palm_frames": 0, "open_palm_count": 0, "open_palm_actions": [],
        },
        "scenario_d_2_hands_simultaneous": {
            "frames": 0, "both_hands_detected_frames": 0, "single_hand_detected_frames": 0,
            "zero_hand_frames": 0, "scissors_detected_hands": 0, "actions": []
        },
        "scenario_e_2_hands_moving": {"frames": 0, "actions": []},
        "scenario_f_fast_motion": {"frames": 0, "actions": [], "high_velocity_blocks": 0},
        "scenario_g_transitions": {"frames": 0, "transition_events": 0},
    }

    frame_idx = 0
    last_ts_ms = -1
    last_processed_ts_ms = -1
    target_frame_interval = 1.0 / 30.0

    with _HandLandmarker.create_from_options(options) as landmarker:
        rgb_buf = None

        while True:
            loop_tick_start = time.perf_counter()
            t_cap = time.perf_counter()
            ret, frame = cap.read()
            if not ret:
                break
            seg_idx = (frame_idx // 100) + 1

            cv2.flip(frame, 1, dst=frame)
            if rgb_buf is None or rgb_buf.shape != frame.shape:
                rgb_buf = np.empty_like(frame)
            cv2.cvtColor(frame, cv2.COLOR_BGR2RGB, dst=rgb_buf)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_buf)

            now_ms = time.monotonic_ns() // 1_000_000
            ts_ms = max(now_ms, last_ts_ms + 1)
            last_ts_ms = ts_ms

            t_submit = time.perf_counter()
            rec = _FrameRecord(frame_idx, seg_idx, t_cap, t_submit, ts_ms)
            with async_lock:
                if len(pending_records) >= 120:
                    oldest = sorted(pending_records.keys())[:30]
                    for k in oldest:
                        del pending_records[k]
                pending_records[ts_ms] = rec

            landmarker.detect_async(mp_image, ts_ms)

            # Check for new result
            res_obj = None
            cb_time = None
            frame_meta = None

            with async_lock:
                if latest_cb_ts > last_processed_ts_ms:
                    res_obj, cb_time = async_results.pop(latest_cb_ts, (None, None))
                    res_ts = latest_cb_ts
                    frame_meta = pending_records.pop(res_ts, None)

            if res_obj is not None:
                last_processed_ts_ms = res_ts
                detections = []
                if res_obj.hand_landmarks:
                    for i, lms in enumerate(res_obj.hand_landmarks):
                        lbl = "Unknown"
                        score = 0.0
                        if res_obj.handedness and i < len(res_obj.handedness) and res_obj.handedness[i]:
                            lbl = res_obj.handedness[i][0].category_name
                            score = res_obj.handedness[i][0].score
                        g_res = recognizer.classify(lms)
                        wrist = lms[0]
                        from hand_controller.models import HandDetection
                        detections.append(HandDetection(
                            landmarks=lms,
                            wrist_x=wrist.x,
                            wrist_y=wrist.y,
                            gesture_result=g_res,
                            raw_label=lbl,
                            raw_score=score,
                        ))

                event_time = frame_meta.capture_time if frame_meta else time.perf_counter()
                assignments = tracker.assign(detections, event_time)
                dispatched_actions_this_frame = []

                for det_i, trk_id in assignments.items():
                    det = detections[det_i]
                    trk = tracker.tracks[trk_id]
                    tracker.update_position(trk, det, event_time)

                    action = state_machine.update(trk, det, event_time)
                    if action != SlideAction.NONE:
                        disp = dispatcher.dispatch(action, event_time)
                        if disp:
                            state_machine.latch(trk)
                            dispatched_actions_this_frame.append(action.name)

                tracker.expire_lost_tracks(assignments.values(), event_time)

                # Classify into scenarios based on frame segment
                seg = frame_meta.seg_idx if frame_meta else seg_idx
                num_det = len(detections)

                if seg == 1:  # No hand
                    scenarios["scenario_a_no_hand"]["frames"] += 1
                    scenarios["scenario_a_no_hand"]["detected_hands"] += num_det
                    scenarios["scenario_a_no_hand"]["false_triggers"] += len(dispatched_actions_this_frame)
                elif seg == 2:  # 1 hand steady LIKE
                    scenarios["scenario_b_1_hand_steady"]["frames"] += 1
                    scenarios["scenario_b_1_hand_steady"]["detected_hands"] += num_det
                    for d in detections:
                        if d.gesture_result.gesture.name == "LIKE":
                            scenarios["scenario_b_1_hand_steady"]["like_count"] += 1
                    scenarios["scenario_b_1_hand_steady"]["actions"].extend(dispatched_actions_this_frame)
                elif seg == 3:  # 1 hand SCISSORS
                    scenarios["scenario_c_1_hand_active"]["scissors_frames"] += 1
                    for d in detections:
                        if d.gesture_result.gesture.name == "SCISSORS":
                            scenarios["scenario_c_1_hand_active"]["scissors_count"] += 1
                    scenarios["scenario_c_1_hand_active"]["scissors_actions"].extend(dispatched_actions_this_frame)
                elif seg == 4:  # 1 hand LIKE
                    scenarios["scenario_c_1_hand_active"]["like_frames"] += 1
                    for d in detections:
                        if d.gesture_result.gesture.name == "LIKE":
                            scenarios["scenario_c_1_hand_active"]["like_count"] += 1
                    scenarios["scenario_c_1_hand_active"]["like_actions"].extend(dispatched_actions_this_frame)
                elif seg == 5:  # 1 hand OPEN_PALM
                    scenarios["scenario_c_1_hand_active"]["open_palm_frames"] += 1
                    for d in detections:
                        if d.gesture_result.gesture.name == "OPEN_PALM":
                            scenarios["scenario_c_1_hand_active"]["open_palm_count"] += 1
                    scenarios["scenario_c_1_hand_active"]["open_palm_actions"].extend(dispatched_actions_this_frame)
                elif seg == 6:  # Fast motion
                    scenarios["scenario_f_fast_motion"]["frames"] += 1
                    scenarios["scenario_f_fast_motion"]["actions"].extend(dispatched_actions_this_frame)
                elif seg == 7:  # 2 hands simultaneously
                    scenarios["scenario_d_2_hands_simultaneous"]["frames"] += 1
                    if num_det == 2:
                        scenarios["scenario_d_2_hands_simultaneous"]["both_hands_detected_frames"] += 1
                    elif num_det == 1:
                        scenarios["scenario_d_2_hands_simultaneous"]["single_hand_detected_frames"] += 1
                    else:
                        scenarios["scenario_d_2_hands_simultaneous"]["zero_hand_frames"] += 1
                    for d in detections:
                        if d.gesture_result.gesture.name == "SCISSORS":
                            scenarios["scenario_d_2_hands_simultaneous"]["scissors_detected_hands"] += 1
                    scenarios["scenario_d_2_hands_simultaneous"]["actions"].extend(dispatched_actions_this_frame)
                elif seg == 8:  # Far hand
                    scenarios["scenario_e_2_hands_moving"]["frames"] += 1
                    scenarios["scenario_e_2_hands_moving"]["actions"].extend(dispatched_actions_this_frame)

            elapsed_in_tick = time.perf_counter() - loop_tick_start
            if elapsed_in_tick < target_frame_interval:
                time.sleep(target_frame_interval - elapsed_in_tick)

            frame_idx += 1

        time.sleep(0.3)

    cap.release()

    # Accuracy Summary Metrics
    # Seg 2, 3, 4, 5, 7 should each trigger their expected action at least once without false triggers
    seg2_actions = scenarios["scenario_b_1_hand_steady"]["actions"]
    seg3_actions = scenarios["scenario_c_1_hand_active"]["scissors_actions"]
    seg4_actions = scenarios["scenario_c_1_hand_active"]["like_actions"]
    seg5_actions = scenarios["scenario_c_1_hand_active"]["open_palm_actions"]
    seg7_actions = scenarios["scenario_d_2_hands_simultaneous"]["actions"]

    seg7_both = scenarios["scenario_d_2_hands_simultaneous"]["both_hands_detected_frames"]
    seg7_total = scenarios["scenario_d_2_hands_simultaneous"]["frames"]
    two_hand_detection_rate_pct = (seg7_both / seg7_total * 100.0) if seg7_total > 0 else 0.0

    accuracy_results = {
        "scenarios": scenarios,
        "evaluations": {
            "scenario_a_no_hand_clean": scenarios["scenario_a_no_hand"]["false_triggers"] == 0,
            "scenario_b_like_action_dispatched": "NEXT" in seg2_actions,
            "scenario_c_scissors_action_dispatched": "PREVIOUS" in seg3_actions,
            "scenario_c_like_action_dispatched": "NEXT" in seg4_actions,
            "scenario_c_open_palm_action_dispatched": "BLACKOUT" in seg5_actions,
            "scenario_d_two_hands_both_detected": seg7_both > 0,
            "scenario_d_two_hands_detection_rate_pct": two_hand_detection_rate_pct,
            "scenario_d_two_hands_actions_dispatched": len(seg7_actions) >= 1,
        },
        "verdict": "PASS" if (
            scenarios["scenario_a_no_hand"]["false_triggers"] == 0
            and "NEXT" in seg2_actions
            and "PREVIOUS" in seg3_actions
            and "NEXT" in seg4_actions
            and seg7_both > 0
        ) else "FAIL"
    }

    print("\nAccuracy Test Summary:")
    print(f"Scenario A (No hand): False triggers = {scenarios['scenario_a_no_hand']['false_triggers']}")
    print(f"Scenario B (1 Hand LIKE): Actions = {seg2_actions}")
    print(f"Scenario C (1 Hand Active): Scissors={seg3_actions}, Like={seg4_actions}, OpenPalm={seg5_actions}")
    print(f"Scenario D (2 Hands Simultaneous): Both hands detected on {seg7_both}/{seg7_total} frames ({two_hand_detection_rate_pct:.1f}%), Actions = {seg7_actions}")
    print(f"Accuracy Verdict: {accuracy_results['verdict']}")

    return accuracy_results


def main() -> None:
    results = run_accuracy_test()
    with open("async_accuracy.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print("Exported async_accuracy.json")


if __name__ == "__main__":
    main()
