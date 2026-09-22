"""Production v2 Complete Validation & Benchmark Suite.

Executes:
1. Full 800-frame End-to-End Pipeline Benchmark (pacing 30 FPS, num_hands=2, ABOVE_NORMAL priority).
   Exports: production_v2_benchmark.json, production_v2_frames.csv
2. Full Accuracy & Scenario Audit (Scenarios A through G).
   Exports: production_v2_accuracy.json
3. 5-Trial Startup Validation (Trial 1 Cold, Trials 2-5 Warm, constructor params).
   Exports: production_v2_startup.json
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


def run_production_v2_benchmark(video_path: str = "benchmark_input.mp4") -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    print("\n=======================================================")
    print("RUNNING PRODUCTION V2 END-TO-END BENCHMARK (ABOVE_NORMAL)")
    print("=======================================================")

    proc = psutil.Process(os.getpid())
    try:
        proc.nice(psutil.ABOVE_NORMAL_PRIORITY_CLASS)
    except Exception:
        pass

    cfg = AppConfig(
        running_mode="LIVE_STREAM_WORKER",
        num_hands=2,
        min_hand_detection_confidence=0.25,
        min_hand_presence_confidence=0.25,
        min_tracking_confidence=0.25,
        preview_enabled=False,
    )
    recognizer = GestureRecognizer(cfg.gesture)
    tracker = HandTracker(cfg.tracking)
    state_machine = GestureStateMachine(cfg.tracking)
    dispatcher = ActionDispatcher(cooldown=cfg.global_action_cooldown)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video {video_path}")

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
    gesture_done_to_action_start_ms = []
    action_duration_ms = []
    capture_to_action_ms = []

    cpu_samples = []
    ram_samples = []
    frame_csv_rows = []

    # Scenario Tracking
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
        "scenario_f_fast_motion": {"frames": 0, "actions": []},
        "scenario_g_transitions": {"frames": 0, "transition_events": 0},
    }

    seg_actions = {i: [] for i in range(1, 9)}
    seg7_both_hands_frames = 0
    seg7_total_frames = 0

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
            num_det = len(detections)

            if seg == 7:
                seg7_total_frames += 1
                if num_det >= 2:
                    seg7_both_hands_frames += 1

            event_time = packet.capture_time
            assignments = tracker.assign(detections, event_time)
            dispatched_this_frame = []

            for det_i, trk_id in assignments.items():
                det = detections[det_i]
                trk = tracker.tracks[trk_id]
                tracker.update_position(trk, det, event_time)
                action = state_machine.update(trk, det, event_time)
                if action != SlideAction.NONE:
                    t_pre_disp = time.perf_counter()
                    gesture_done_to_action_start_ms.append((t_pre_disp - t_gest_start) * 1000.0)

                    t_act_start = time.perf_counter()
                    disp = dispatcher.dispatch(action, event_time)
                    t_act_end = time.perf_counter()
                    action_duration_ms.append((t_act_end - t_act_start) * 1000.0)

                    if disp:
                        state_machine.latch(trk)
                        seg_actions[seg].append(action.name)
                        dispatched_this_frame.append(action.name)
                        capture_to_action_ms.append((t_act_end - packet.capture_time) * 1000.0)

            tracker.expire_lost_tracks(assignments.values(), event_time)
            t_gest_end = time.perf_counter()

            cb_to_gest = (t_gest_end - packet.callback_time) * 1000.0
            cap_to_gest = (t_gest_end - packet.capture_time) * 1000.0
            callback_to_gesture_ms.append(cb_to_gest)
            capture_to_gesture_ms.append(cap_to_gest)

            # Accuracy Scenarios Tracking
            if seg == 1:
                scenarios["scenario_a_no_hand"]["frames"] += 1
                scenarios["scenario_a_no_hand"]["detected_hands"] += num_det
                scenarios["scenario_a_no_hand"]["false_triggers"] += len(dispatched_this_frame)
            elif seg == 2:
                scenarios["scenario_b_1_hand_steady"]["frames"] += 1
                scenarios["scenario_b_1_hand_steady"]["detected_hands"] += num_det
                for d in detections:
                    if d.gesture_result.gesture.name == "LIKE":
                        scenarios["scenario_b_1_hand_steady"]["like_count"] += 1
                scenarios["scenario_b_1_hand_steady"]["actions"].extend(dispatched_this_frame)
            elif seg == 3:
                scenarios["scenario_c_1_hand_active"]["scissors_frames"] += 1
                for d in detections:
                    if d.gesture_result.gesture.name == "SCISSORS":
                        scenarios["scenario_c_1_hand_active"]["scissors_count"] += 1
                scenarios["scenario_c_1_hand_active"]["scissors_actions"].extend(dispatched_this_frame)
            elif seg == 4:
                scenarios["scenario_c_1_hand_active"]["like_frames"] += 1
                for d in detections:
                    if d.gesture_result.gesture.name == "LIKE":
                        scenarios["scenario_c_1_hand_active"]["like_count"] += 1
                scenarios["scenario_c_1_hand_active"]["like_actions"].extend(dispatched_this_frame)
            elif seg == 5:
                scenarios["scenario_c_1_hand_active"]["open_palm_frames"] += 1
                for d in detections:
                    if d.gesture_result.gesture.name == "OPEN_PALM":
                        scenarios["scenario_c_1_hand_active"]["open_palm_count"] += 1
                scenarios["scenario_c_1_hand_active"]["open_palm_actions"].extend(dispatched_this_frame)
            elif seg == 6:
                scenarios["scenario_f_fast_motion"]["frames"] += 1
                scenarios["scenario_f_fast_motion"]["actions"].extend(dispatched_this_frame)
            elif seg == 7:
                scenarios["scenario_d_2_hands_simultaneous"]["frames"] += 1
                if num_det == 2:
                    scenarios["scenario_d_2_hands_simultaneous"]["both_hands_detected_frames"] += 1
                elif num_det == 1:
                    scenarios["scenario_d_2_hands_simultaneous"]["single_hand_detected_frames"] += 1
                else:
                    scenarios["scenario_d_2_hands_simultaneous"]["zero_hand_frames"] += 1
                scenarios["scenario_d_2_hands_simultaneous"]["actions"].extend(dispatched_this_frame)
            elif seg == 8:
                scenarios["scenario_e_2_hands_moving"]["frames"] += 1
                scenarios["scenario_e_2_hands_moving"]["actions"].extend(dispatched_this_frame)

            frame_csv_rows.append({
                "frame_idx": packet.frame_idx,
                "seg_idx": packet.seg_idx,
                "timestamp_ms": packet.timestamp_ms,
                "capture_to_submit_ms": round((packet.submit_time - packet.capture_time) * 1000.0, 3),
                "submit_to_callback_ms": round(sub_to_cb, 3),
                "frame_age_ms": round(cap_to_cb, 3),
                "callback_to_gesture_ms": round(cb_to_gest, 3),
                "capture_to_gesture_ms": round(cap_to_gest, 3),
            })

    control_thread = threading.Thread(target=worker_loop, name="ProdV2WorkerThread", daemon=False)
    control_thread.start()

    frame_idx = 0
    last_ts_ms = -1
    rgb_buf = None
    target_frame_interval = 1.0 / 30.0

    ram_start_mb = proc.memory_info().rss / (1024 * 1024)
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

            if frame_idx % 20 == 0:
                cpu_samples.append(proc.cpu_percent(interval=None))
                ram_samples.append(proc.memory_info().rss / (1024 * 1024))

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
    total_wall_s = wall_end - wall_start

    ram_end_mb = proc.memory_info().rss / (1024 * 1024)
    two_hand_rate_pct = (seg7_both_hands_frames / seg7_total_frames * 100.0) if seg7_total_frames > 0 else 0.0

    benchmark_summary = {
        "pipeline": "PRODUCTION_V2 (LIVE_STREAM_WORKER, Constructor Params, ABOVE_NORMAL)",
        "wall_time_s": round(total_wall_s, 2),
        "counts": {
            "captured_frames": captured_count,
            "submitted_frames": submitted_count,
            "callbacks_received": callback_count,
            "processed_results": processed_count,
            "mediapipe_input_drops": mediapipe_input_drop,
            "control_overwrites": control_overwrite_drop,
            "metadata_evictions": metadata_eviction,
            "total_drops": mediapipe_input_drop + control_overwrite_drop + metadata_eviction,
        },
        "fps": {
            "captured_fps": round(captured_count / total_wall_s, 2),
            "submitted_fps": round(submitted_count / total_wall_s, 2),
            "callback_fps": round(callback_count / total_wall_s, 2),
            "processed_fps": round(processed_count / total_wall_s, 2),
        },
        "drop_rates": {
            "mediapipe_drop_pct": round(mediapipe_input_drop / submitted_count * 100.0, 2) if submitted_count > 0 else 0.0,
            "control_overwrite_pct": round(control_overwrite_drop / callback_count * 100.0, 2) if callback_count > 0 else 0.0,
        },
        "latencies": {
            "capture_to_submit_ms": compute_stats(capture_to_submit_ms),
            "submit_to_callback_ms": compute_stats(submit_to_callback_ms),
            "capture_to_callback_ms": compute_stats(capture_to_callback_ms),
            "callback_to_gesture_ms": compute_stats(callback_to_gesture_ms),
            "capture_to_gesture_ms": compute_stats(capture_to_gesture_ms),
            "gesture_done_to_action_start_ms": compute_stats(gesture_done_to_action_start_ms),
            "action_duration_ms": compute_stats(action_duration_ms),
            "capture_to_action_ms": compute_stats(capture_to_action_ms),
        },
        "system": {
            "ram_start_mb": round(ram_start_mb, 2),
            "ram_end_mb": round(ram_end_mb, 2),
            "ram_peak_mb": round(max(ram_samples) if ram_samples else ram_end_mb, 2),
            "cpu_mean_pct": round(float(np.mean(cpu_samples)) if cpu_samples else 0.0, 2),
            "cpu_p95_pct": round(float(np.percentile(cpu_samples, 95)) if cpu_samples else 0.0, 2),
        },
        "accuracy": {
            "seg7_both_hands_frames": seg7_both_hands_frames,
            "seg7_total_frames": seg7_total_frames,
            "two_hand_detection_rate_pct": round(two_hand_rate_pct, 1),
            "actions_dispatched": seg_actions,
        },
    }

    accuracy_summary = {
        "scenarios": scenarios,
        "evaluations": {
            "scenario_a_no_hand_clean": scenarios["scenario_a_no_hand"]["false_triggers"] == 0,
            "scenario_b_like_action_dispatched": "NEXT" in scenarios["scenario_b_1_hand_steady"]["actions"],
            "scenario_c_scissors_action_dispatched": "PREVIOUS" in scenarios["scenario_c_1_hand_active"]["scissors_actions"],
            "scenario_c_like_action_dispatched": "NEXT" in scenarios["scenario_c_1_hand_active"]["like_actions"],
            "scenario_c_open_palm_action_dispatched": "BLACKOUT" in scenarios["scenario_c_1_hand_active"]["open_palm_actions"],
            "scenario_d_two_hands_both_detected": scenarios["scenario_d_2_hands_simultaneous"]["both_hands_detected_frames"] > 0,
            "scenario_d_two_hands_detection_rate_pct": round(two_hand_rate_pct, 1),
            "scenario_d_two_hands_actions_dispatched": len(scenarios["scenario_d_2_hands_simultaneous"]["actions"]) >= 1,
        },
        "verdict": "PASS" if (
            scenarios["scenario_a_no_hand"]["false_triggers"] == 0
            and "NEXT" in scenarios["scenario_b_1_hand_steady"]["actions"]
            and "PREVIOUS" in scenarios["scenario_c_1_hand_active"]["scissors_actions"]
            and "NEXT" in scenarios["scenario_c_1_hand_active"]["like_actions"]
            and seg7_both_hands_frames > 0
        ) else "FAIL"
    }

    print("\nBenchmark Finished:")
    print(f"- Submitted FPS: {benchmark_summary['fps']['submitted_fps']}, Processed FPS: {benchmark_summary['fps']['processed_fps']}")
    print(f"- Callback->Gesture Mean: {benchmark_summary['latencies']['callback_to_gesture_ms']['mean']:.2f} ms (P95: {benchmark_summary['latencies']['callback_to_gesture_ms']['p95']:.2f} ms)")
    print(f"- Capture->Action Mean: {benchmark_summary['latencies']['capture_to_action_ms']['mean']:.2f} ms (Median: {benchmark_summary['latencies']['capture_to_action_ms']['median']:.2f} ms)")
    print(f"- 2-Hand Rate: {two_hand_rate_pct:.1f}%")
    print(f"- Accuracy Verdict: {accuracy_summary['verdict']}")

    return benchmark_summary, frame_csv_rows, accuracy_summary


def run_startup_trials(num_trials: int = 5) -> dict[str, Any]:
    print("\n=======================================================")
    print(f"RUNNING PRODUCTION V2 STARTUP VALIDATION ({num_trials} Trials)")
    print("=======================================================")

    trials = []
    cfg = AppConfig()

    for t in range(1, num_trials + 1):
        is_cold = (t == 1)
        trial_type = "Cold (Trial 1)" if is_cold else f"Warm Repeated (#{t})"
        print(f"Running {trial_type}...", end=" ", flush=True)

        t_start = time.perf_counter_ns()

        # Phase 1: VideoCapture constructor with direct parameters (A1)
        t0 = time.perf_counter_ns()
        params = [
            cv2.CAP_PROP_FRAME_WIDTH, cfg.camera.width,
            cv2.CAP_PROP_FRAME_HEIGHT, cfg.camera.height,
            cv2.CAP_PROP_FPS, cfg.camera.fps,
        ]
        cap = cv2.VideoCapture(cfg.camera.index, cv2.CAP_MSMF, params)
        t1 = time.perf_counter_ns()
        cam_ctor_ms = (t1 - t0) / 1e6

        # Phase 2: First successful frame
        t2 = time.perf_counter_ns()
        ret, frame = cap.read()
        t3 = time.perf_counter_ns()
        first_frame_ms = (t3 - t2) / 1e6

        actual_w = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
        actual_h = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
        actual_fps = cap.get(cv2.CAP_PROP_FPS)

        # Phase 3: MediaPipe initialization
        t4 = time.perf_counter_ns()
        options = _HandLandmarkerOptions(
            base_options=_BaseOptions(model_asset_path=cfg.model_path),
            running_mode=_RunningMode.LIVE_STREAM,
            num_hands=cfg.num_hands,
            min_hand_detection_confidence=cfg.min_hand_detection_confidence,
            min_hand_presence_confidence=cfg.min_hand_presence_confidence,
            min_tracking_confidence=cfg.min_tracking_confidence,
            result_callback=lambda res, img, ts: None,
        )
        landmarker = _HandLandmarker.create_from_options(options)
        t5 = time.perf_counter_ns()
        mp_init_ms = (t5 - t4) / 1e6

        # Phase 4: First inference
        t6 = time.perf_counter_ns()
        if ret and frame is not None:
            cv2.flip(frame, 1, dst=frame)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            landmarker.detect_async(mp_image, 1)
        t7 = time.perf_counter_ns()
        first_infer_ms = (t7 - t6) / 1e6

        # Teardown
        landmarker.close()
        cap.release()
        t_end = time.perf_counter_ns()
        total_usable_ms = (t_end - t_start) / 1e6

        print(f"Total: {total_usable_ms:.1f} ms (Ctor: {cam_ctor_ms:.1f} ms, 1stFrame: {first_frame_ms:.1f} ms, Model: {mp_init_ms:.1f} ms)")

        trials.append({
            "trial": t,
            "type": "cold" if is_cold else "warm",
            "videocapture_constructor_ms": round(cam_ctor_ms, 2),
            "first_frame_read_ms": round(first_frame_ms, 2),
            "mediapipe_init_ms": round(mp_init_ms, 2),
            "first_infer_submit_ms": round(first_infer_ms, 2),
            "total_usable_startup_ms": round(total_usable_ms, 2),
            "actual_resolution": f"{int(actual_w)}x{int(actual_h)}",
            "actual_fps": round(actual_fps, 1),
            "success": ret,
        })
        time.sleep(1.0)

    cold_trial = trials[0]
    warm_trials = trials[1:]

    startup_summary = {
        "trials_count": num_trials,
        "cold_startup_trial_1": cold_trial,
        "warm_startups_trials_2_to_5": {
            "trials": warm_trials,
            "mean_constructor_ms": round(float(np.mean([t["videocapture_constructor_ms"] for t in warm_trials])), 2),
            "mean_first_frame_ms": round(float(np.mean([t["first_frame_read_ms"] for t in warm_trials])), 2),
            "mean_mediapipe_init_ms": round(float(np.mean([t["mediapipe_init_ms"] for t in warm_trials])), 2),
            "mean_total_usable_startup_ms": round(float(np.mean([t["total_usable_startup_ms"] for t in warm_trials])), 2),
        },
        "all_trials": trials,
    }
    return startup_summary


def main():
    # 1. Benchmark & Accuracy
    b_summary, csv_rows, acc_summary = run_production_v2_benchmark()
    with open("production_v2_benchmark.json", "w", encoding="utf-8") as f:
        json.dump(b_summary, f, indent=2)

    if csv_rows:
        with open("production_v2_frames.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(csv_rows[0].keys()))
            writer.writeheader()
            writer.writerows(csv_rows)

    with open("production_v2_accuracy.json", "w", encoding="utf-8") as f:
        json.dump(acc_summary, f, indent=2)

    # 2. 5-Trial Startup Validation
    startup_summary = run_startup_trials(5)
    with open("production_v2_startup.json", "w", encoding="utf-8") as f:
        json.dump(startup_summary, f, indent=2)

    print("\nSaved production_v2_benchmark.json, production_v2_frames.csv, production_v2_accuracy.json, and production_v2_startup.json successfully!")


if __name__ == "__main__":
    main()
