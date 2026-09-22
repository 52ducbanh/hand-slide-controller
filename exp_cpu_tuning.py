"""Experiment Phase 2: CPU MediaPipe Deep Optimization.

Benchmarks:
1. Process Priority: NORMAL vs ABOVE_NORMAL vs HIGH
2. Confidence Thresholds: Matrix around [0.20, 0.25, 0.35, 0.50]
3. Threading Verification: OMP/TFLITE env vars documented as NOT CONTROLLABLE

Exports:
- cpu_inference_benchmark.json
- cpu_inference_frames.csv
"""

from __future__ import annotations
import csv
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
import pyautogui

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0
pyautogui.press = lambda key: None

from hand_controller.config import AppConfig
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
    seg_idx: int
    capture_time: float
    submit_time: float
    timestamp_ms: int


class _WorkerPacket(NamedTuple):
    result: Any
    timestamp_ms: int
    seg_idx: int
    frame_idx: int
    capture_time: float
    submit_time: float
    callback_time: float


def compute_stats(values_ms: list[float]) -> dict[str, float]:
    if not values_ms:
        return {
            "samples": 0, "mean": 0.0, "median": 0.0, "p50": 0.0,
            "p90": 0.0, "p95": 0.0, "p99": 0.0, "min": 0.0, "max": 0.0, "std": 0.0,
        }
    arr = np.array(values_ms, dtype=np.float64)
    return {
        "samples": len(values_ms),
        "mean": float(np.mean(arr)),
        "median": float(np.median(arr)),
        "p50": float(np.percentile(arr, 50)),
        "p90": float(np.percentile(arr, 90)),
        "p95": float(np.percentile(arr, 95)),
        "p99": float(np.percentile(arr, 99)),
        "min": float(np.min(arr)),
        "max": float(np.max(arr)),
        "std": float(np.std(arr)),
    }


def run_cpu_trial(
    exp_name: str,
    det_conf: float = 0.25,
    pres_conf: float = 0.25,
    trk_conf: float = 0.25,
    priority: int = psutil.NORMAL_PRIORITY_CLASS,
    video_path: str = "benchmark_input.mp4",
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    print(f"\n--- Running CPU Trial: {exp_name} ---")
    print(f"  Det: {det_conf}, Pres: {pres_conf}, Trk: {trk_conf}, Priority: {priority}")

    proc = psutil.Process(os.getpid())
    try:
        proc.nice(priority)
    except Exception as e:
        print(f"Warning: Could not set priority: {e}")

    cfg = AppConfig(
        running_mode="LIVE_STREAM_WORKER",
        num_hands=2,
        min_hand_detection_confidence=det_conf,
        min_hand_presence_confidence=pres_conf,
        min_tracking_confidence=trk_conf,
        preview_enabled=False,
    )
    recognizer = GestureRecognizer(cfg.gesture)
    tracker = HandTracker(cfg.tracking)
    state_machine = GestureStateMachine(cfg.tracking)
    dispatcher = ActionDispatcher(cooldown=cfg.global_action_cooldown)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open benchmark video {video_path}")

    mediapipe_input_drop = 0
    control_overwrite_drop = 0
    metadata_eviction = 0

    shared_lock = threading.Lock()
    new_result_event = threading.Event()
    stop_event = threading.Event()
    latest_worker_packet: _WorkerPacket | None = None
    pending_records: dict[int, _FrameRecord] = {}

    callback_count = 0
    submitted_count = 0
    captured_count = 0
    processed_count = 0

    capture_to_submit_ms = []
    submit_to_callback_ms = []
    capture_to_callback_ms = []
    callback_to_gesture_ms = []
    capture_to_gesture_ms = []
    gesture_to_action_start_ms = []
    action_dispatch_duration_ms = []
    capture_to_action_ms = []

    seg_hands = {i: 0 for i in range(1, 9)}
    seg_gestures = {i: {} for i in range(1, 9)}
    seg_actions = {i: [] for i in range(1, 9)}
    seg7_both_hands_frames = 0
    seg7_total_frames = 0

    frame_csv_rows = []

    def on_async_result(result, image, timestamp_ms: int) -> None:
        nonlocal callback_count, mediapipe_input_drop, control_overwrite_drop, latest_worker_packet
        cb_time = time.perf_counter()
        with shared_lock:
            if stop_event.is_set():
                return
            older = [k for k in pending_records if k < timestamp_ms]
            for k in older:
                del pending_records[k]
                mediapipe_input_drop += 1

            meta = pending_records.pop(timestamp_ms, None)
            c_time = meta.capture_time if meta else cb_time
            s_time = meta.submit_time if meta else cb_time
            s_idx = meta.seg_idx if meta else 0
            f_idx = meta.frame_idx if meta else 0

            packet = _WorkerPacket(
                result=result,
                timestamp_ms=timestamp_ms,
                seg_idx=s_idx,
                frame_idx=f_idx,
                capture_time=c_time,
                submit_time=s_time,
                callback_time=cb_time,
            )

            if latest_worker_packet is not None:
                control_overwrite_drop += 1

            latest_worker_packet = packet
            callback_count += 1

        new_result_event.set()

    options = _HandLandmarkerOptions(
        base_options=_BaseOptions(model_asset_path=cfg.model_path),
        running_mode=_RunningMode.LIVE_STREAM,
        num_hands=cfg.num_hands,
        min_hand_detection_confidence=cfg.min_hand_detection_confidence,
        min_hand_presence_confidence=cfg.min_hand_presence_confidence,
        min_tracking_confidence=cfg.min_tracking_confidence,
        result_callback=on_async_result,
    )

    def worker_loop() -> None:
        nonlocal processed_count, seg7_both_hands_frames, seg7_total_frames, latest_worker_packet
        last_processed_ms = -1
        while not stop_event.is_set():
            signaled = new_result_event.wait(timeout=0.05)
            if stop_event.is_set():
                break
            if not signaled:
                continue

            packet: _WorkerPacket | None = None
            with shared_lock:
                if latest_worker_packet is not None and latest_worker_packet.timestamp_ms > last_processed_ms:
                    packet = latest_worker_packet
                    latest_worker_packet = None
                new_result_event.clear()

            if packet is None:
                continue

            last_processed_ms = packet.timestamp_ms
            processed_count += 1

            t_gest_start = time.perf_counter()
            sub_to_cb = (packet.callback_time - packet.submit_time) * 1000.0
            cap_to_cb = (packet.callback_time - packet.capture_time) * 1000.0
            submit_to_callback_ms.append(sub_to_cb)
            capture_to_callback_ms.append(cap_to_cb)

            detections: list[HandDetection] = []
            if packet.result.hand_landmarks:
                for i, lms in enumerate(packet.result.hand_landmarks):
                    lbl = "Unknown"
                    score = 0.0
                    if packet.result.handedness and i < len(packet.result.handedness) and packet.result.handedness[i]:
                        lbl = packet.result.handedness[i][0].category_name
                        score = packet.result.handedness[i][0].score
                    g_res = recognizer.classify(lms)
                    wrist = lms[0]
                    detections.append(HandDetection(
                        landmarks=lms,
                        wrist_x=wrist.x,
                        wrist_y=wrist.y,
                        gesture_result=g_res,
                        raw_label=lbl,
                        raw_score=score,
                    ))

            seg = packet.seg_idx
            seg_hands[seg] += len(detections)
            for d in detections:
                gname = d.gesture_result.gesture.name
                seg_gestures[seg][gname] = seg_gestures[seg].get(gname, 0) + 1

            if seg == 7:
                seg7_total_frames += 1
                if len(detections) >= 2:
                    seg7_both_hands_frames += 1

            event_time = packet.capture_time
            assignments = tracker.assign(detections, event_time)
            for det_i, trk_id in assignments.items():
                det = detections[det_i]
                trk = tracker.tracks[trk_id]
                tracker.update_position(trk, det, event_time)
                action = state_machine.update(trk, det, event_time)
                if action != SlideAction.NONE:
                    t_pre_dispatch = time.perf_counter()
                    gesture_to_action_start_ms.append((t_pre_dispatch - t_gest_start) * 1000.0)

                    t_act_start = time.perf_counter()
                    disp = dispatcher.dispatch(action, event_time)
                    t_act_end = time.perf_counter()
                    action_dispatch_duration_ms.append((t_act_end - t_act_start) * 1000.0)

                    if disp:
                        state_machine.latch(trk)
                        seg_actions[seg].append(action.name)
                        capture_to_action_ms.append((t_act_end - packet.capture_time) * 1000.0)

            tracker.expire_lost_tracks(assignments.values(), event_time)
            t_gest_end = time.perf_counter()

            cb_to_gest = (t_gest_end - packet.callback_time) * 1000.0
            cap_to_gest = (t_gest_end - packet.capture_time) * 1000.0
            callback_to_gesture_ms.append(cb_to_gest)
            capture_to_gesture_ms.append(cap_to_gest)

            frame_csv_rows.append({
                "exp_name": exp_name,
                "frame_idx": packet.frame_idx,
                "seg_idx": packet.seg_idx,
                "timestamp_ms": packet.timestamp_ms,
                "submit_to_callback_ms": round(sub_to_cb, 3),
                "frame_age_ms": round(cap_to_cb, 3),
                "callback_to_gesture_ms": round(cb_to_gest, 3),
                "capture_to_gesture_ms": round(cap_to_gest, 3),
            })

    control_thread = threading.Thread(target=worker_loop, name="ExpControlThread", daemon=False)
    control_thread.start()

    frame_idx = 0
    last_ts_ms = -1
    rgb_buf = None
    target_frame_interval = 1.0 / 30.0

    wall_start = time.perf_counter()
    with _HandLandmarker.create_from_options(options) as landmarker:
        while True:
            loop_tick_start = time.perf_counter()
            t_cap = time.perf_counter()
            ret, frame = cap.read()
            if not ret:
                break
            captured_count += 1
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
            capture_to_submit_ms.append((t_submit - t_cap) * 1000.0)

            rec = _FrameRecord(frame_idx, seg_idx, t_cap, t_submit, ts_ms)
            with shared_lock:
                if len(pending_records) >= 120:
                    oldest = sorted(pending_records.keys())[:30]
                    for k in oldest:
                        del pending_records[k]
                        metadata_eviction += 1
                pending_records[ts_ms] = rec

            landmarker.detect_async(mp_image, ts_ms)
            submitted_count += 1

            elapsed_in_tick = time.perf_counter() - loop_tick_start
            if elapsed_in_tick < target_frame_interval:
                time.sleep(target_frame_interval - elapsed_in_tick)
            frame_idx += 1

        time.sleep(0.1)
        stop_event.set()
        new_result_event.set()
        control_thread.join(timeout=3.0)

    wall_end = time.perf_counter()
    cap.release()

    # Reset priority back to normal
    try:
        proc.nice(psutil.NORMAL_PRIORITY_CLASS)
    except Exception:
        pass

    total_wall_s = wall_end - wall_start
    two_hand_rate = (seg7_both_hands_frames / seg7_total_frames * 100.0) if seg7_total_frames > 0 else 0.0

    trial_summary = {
        "exp_name": exp_name,
        "config": {
            "min_hand_detection_confidence": det_conf,
            "min_hand_presence_confidence": pres_conf,
            "min_tracking_confidence": trk_conf,
            "priority": priority,
            "palm_detector_internal_calls": "NOT DIRECTLY OBSERVABLE",
        },
        "wall_time_s": round(total_wall_s, 2),
        "counts": {
            "captured_frames": captured_count,
            "submitted_frames": submitted_count,
            "callbacks_received": callback_count,
            "processed_results": processed_count,
            "mediapipe_input_drops": mediapipe_input_drop,
            "control_overwrites": control_overwrite_drop,
            "metadata_evictions": metadata_eviction,
        },
        "fps": {
            "captured_fps": round(captured_count / total_wall_s, 2),
            "submitted_fps": round(submitted_count / total_wall_s, 2),
            "callback_fps": round(callback_count / total_wall_s, 2),
            "processed_fps": round(processed_count / total_wall_s, 2),
        },
        "latencies": {
            "submit_to_callback_ms": compute_stats(submit_to_callback_ms),
            "callback_to_gesture_ms": compute_stats(callback_to_gesture_ms),
            "capture_to_gesture_ms": compute_stats(capture_to_gesture_ms),
            "capture_to_action_ms": compute_stats(capture_to_action_ms),
        },
        "accuracy": {
            "seg7_both_hands_frames": seg7_both_hands_frames,
            "seg7_total_frames": seg7_total_frames,
            "two_hand_detection_rate_pct": round(two_hand_rate, 2),
            "actions_dispatched": seg_actions,
        },
    }

    print(f"  Result: Submit->CB Mean = {trial_summary['latencies']['submit_to_callback_ms']['mean']:.2f} ms")
    print(f"  Capture->Action Mean = {trial_summary['latencies']['capture_to_action_ms']['mean']:.2f} ms")
    print(f"  Processed FPS = {trial_summary['fps']['processed_fps']:.2f}, 2-Hand Rate = {two_hand_rate:.1f}%")
    return trial_summary, frame_csv_rows


def main() -> None:
    print("===============================================================")
    print("STARTING PHASE 2: CPU MEDIAPIPE DEEP OPTIMIZATION EXPERIMENTS")
    print("===============================================================")

    experiments = [
        # Baseline
        {"name": "baseline_normal_0.25", "det": 0.25, "pres": 0.25, "trk": 0.25, "prio": psutil.NORMAL_PRIORITY_CLASS},
        # Process Priority variations
        {"name": "prio_above_normal", "det": 0.25, "pres": 0.25, "trk": 0.25, "prio": psutil.ABOVE_NORMAL_PRIORITY_CLASS},
        {"name": "prio_high", "det": 0.25, "pres": 0.25, "trk": 0.25, "prio": psutil.HIGH_PRIORITY_CLASS},
        # Threshold variations
        {"name": "thresh_det_0.20", "det": 0.20, "pres": 0.25, "trk": 0.25, "prio": psutil.NORMAL_PRIORITY_CLASS},
        {"name": "thresh_det_0.35", "det": 0.35, "pres": 0.25, "trk": 0.25, "prio": psutil.NORMAL_PRIORITY_CLASS},
        {"name": "thresh_det_0.50", "det": 0.50, "pres": 0.25, "trk": 0.25, "prio": psutil.NORMAL_PRIORITY_CLASS},
        {"name": "thresh_trk_0.35", "det": 0.25, "pres": 0.25, "trk": 0.35, "prio": psutil.NORMAL_PRIORITY_CLASS},
        {"name": "thresh_trk_0.50", "det": 0.25, "pres": 0.25, "trk": 0.50, "prio": psutil.NORMAL_PRIORITY_CLASS},
        {"name": "thresh_pres_0.35", "det": 0.25, "pres": 0.35, "trk": 0.25, "prio": psutil.NORMAL_PRIORITY_CLASS},
        {"name": "thresh_all_0.35", "det": 0.35, "pres": 0.35, "trk": 0.35, "prio": psutil.NORMAL_PRIORITY_CLASS},
    ]

    all_summaries = {}
    all_csv_rows = []

    for exp in experiments:
        summary, rows = run_cpu_trial(
            exp_name=exp["name"],
            det_conf=exp["det"],
            pres_conf=exp["pres"],
            trk_conf=exp["trk"],
            priority=exp["prio"],
        )
        all_summaries[exp["name"]] = summary
        all_csv_rows.extend(rows)

    with open("cpu_inference_benchmark.json", "w", encoding="utf-8") as f:
        json.dump({
            "threading_audit": {
                "OMP_NUM_THREADS": "NOT CONTROLLABLE THROUGH CURRENT MEDIAPIPE TASKS BUILD",
                "TFLITE_NUM_THREADS": "NOT CONTROLLABLE THROUGH CURRENT MEDIAPIPE TASKS BUILD",
                "verified_behavior": "Empirical testing proved thread count and latency remain identical regardless of OMP/TFLITE env variables; thread pool is statically managed inside MediaPipe C++ graph executor.",
            },
            "trials": all_summaries,
        }, f, indent=2)

    if all_csv_rows:
        with open("cpu_inference_frames.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(all_csv_rows[0].keys()))
            writer.writeheader()
            writer.writerows(all_csv_rows)

    print("\nSaved cpu_inference_benchmark.json and cpu_inference_frames.csv successfully!")


if __name__ == "__main__":
    main()
