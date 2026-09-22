"""Comprehensive 3-Way Benchmark for Hand Slide Controller Architectures:
1. VIDEO (Synchronous Blocking Baseline)
2. LIVE_STREAM_POLLING (Previous Async Polling Architecture)
3. LIVE_STREAM_WORKER (Event-Driven Control Worker Architecture)

Records all required metrics, drop types, and latency percentiles.
Exports:
- async_before.json (VIDEO mode)
- async_polling.json (LIVE_STREAM_POLLING mode)
- async_after.json (LIVE_STREAM_WORKER mode)
- async_3way_comparison.json (Full side-by-side comparison)
- async_frame_metrics.csv (Per-frame metrics for LIVE_STREAM_WORKER)
"""

from __future__ import annotations
import argparse
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
            "samples": 0,
            "mean": 0.0,
            "median": 0.0,
            "p50": 0.0,
            "p90": 0.0,
            "p95": 0.0,
            "p99": 0.0,
            "min": 0.0,
            "max": 0.0,
            "std": 0.0,
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


def run_single_benchmark(mode: str, video_path: str = "benchmark_input.mp4") -> tuple[dict[str, Any], list[dict[str, Any]]]:
    print(f"\n=======================================================")
    print(f"BENCHMARKING: {mode.upper()} (num_hands=2, 30 FPS pacing)")
    print(f"=======================================================")

    cfg = AppConfig(running_mode=mode, num_hands=2, preview_enabled=False)
    recognizer = GestureRecognizer(cfg.gesture)
    tracker = HandTracker(cfg.tracking)
    state_machine = GestureStateMachine(cfg.tracking)
    dispatcher = ActionDispatcher(cooldown=cfg.global_action_cooldown)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open benchmark video {video_path}")

    # Distinct drop tracking
    mediapipe_input_drop = 0
    control_overwrite_drop = 0
    metadata_eviction = 0

    # Synchronization state
    shared_lock = threading.Lock()
    new_result_event = threading.Event()
    stop_event = threading.Event()
    latest_worker_packet: _WorkerPacket | None = None
    pending_records: dict[int, _FrameRecord] = {}

    callback_count = 0
    submitted_count = 0
    captured_count = 0
    processed_count = 0

    # Latencies
    capture_to_submit_ms = []
    submit_to_callback_ms = []
    capture_to_callback_ms = []   # Frame age
    callback_to_gesture_ms = []   # Primary KPI
    capture_to_gesture_ms = []    # End-to-end
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

            # Prune pending records strictly older than this callback as mediapipe_input_drop
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

    is_live = mode.upper().startswith("LIVE_STREAM")
    is_worker_mode = (mode.upper() == "LIVE_STREAM_WORKER")
    is_polling_mode = (mode.upper() in ["LIVE_STREAM_POLLING", "LIVE_STREAM"])

    options = _HandLandmarkerOptions(
        base_options=_BaseOptions(model_asset_path=cfg.model_path),
        running_mode=_RunningMode.LIVE_STREAM if is_live else _RunningMode.VIDEO,
        num_hands=cfg.num_hands,
        min_hand_detection_confidence=cfg.min_hand_detection_confidence,
        min_hand_presence_confidence=cfg.min_hand_presence_confidence,
        min_tracking_confidence=cfg.min_tracking_confidence,
        result_callback=on_async_result if is_live else None,
    )

    proc = psutil.Process(os.getpid())
    ram_start_mb = proc.memory_info().rss / (1024 * 1024)
    cpu_samples = []
    ram_samples = []

    # Control Worker Thread loop (for LIVE_STREAM_WORKER)
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

            # Latency from callback signal to gesture processing start
            t_gest_start = time.perf_counter()
            sub_to_cb = (packet.callback_time - packet.submit_time) * 1000.0
            cap_to_cb = (packet.callback_time - packet.capture_time) * 1000.0
            submit_to_callback_ms.append(sub_to_cb)
            capture_to_callback_ms.append(cap_to_cb)

            # Build detections & execute recognition
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
                "frame_idx": packet.frame_idx,
                "seg_idx": packet.seg_idx,
                "timestamp_ms": packet.timestamp_ms,
                "capture_to_submit_ms": round((packet.submit_time - packet.capture_time) * 1000.0, 3),
                "submit_to_callback_ms": round(sub_to_cb, 3),
                "frame_age_ms": round(cap_to_cb, 3),
                "callback_to_gesture_ms": round(cb_to_gest, 3),
                "capture_to_gesture_ms": round(cap_to_gest, 3),
                "hands_detected": len(detections),
            })

    control_thread = None
    if is_worker_mode:
        control_thread = threading.Thread(target=worker_loop, daemon=False)
        control_thread.start()

    target_frame_interval = 1.0 / 30.0  # Real 30 FPS pacing
    last_ts_ms = -1
    frame_idx = 0

    wall_start = time.perf_counter()

    with _HandLandmarker.create_from_options(options) as landmarker:
        rgb_buf = None

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

            if is_worker_mode:
                # Mode C: Event-driven worker
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

            elif is_polling_mode:
                # Mode B: Polling in camera loop
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

                packet = None
                with shared_lock:
                    if latest_worker_packet is not None:
                        packet = latest_worker_packet
                        latest_worker_packet = None

                if packet is not None:
                    processed_count += 1
                    t_gest_start = time.perf_counter()
                    sub_to_cb = (packet.callback_time - packet.submit_time) * 1000.0
                    cap_to_cb = (packet.callback_time - packet.capture_time) * 1000.0
                    submit_to_callback_ms.append(sub_to_cb)
                    capture_to_callback_ms.append(cap_to_cb)

                    detections = []
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

            else:
                # Mode A: Synchronous VIDEO baseline
                submitted_count += 1
                t_infer_start = time.perf_counter()
                res_obj = landmarker.detect_for_video(mp_image, ts_ms)
                t_infer_end = time.perf_counter()
                callback_count += 1
                processed_count += 1

                sub_to_cb = (t_infer_end - t_infer_start) * 1000.0
                cap_to_cb = (t_infer_end - t_cap) * 1000.0
                submit_to_callback_ms.append(sub_to_cb)
                capture_to_callback_ms.append(cap_to_cb)

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
                        detections.append(HandDetection(
                            landmarks=lms,
                            wrist_x=wrist.x,
                            wrist_y=wrist.y,
                            gesture_result=g_res,
                            raw_label=lbl,
                            raw_score=score,
                        ))

                seg = seg_idx
                seg_hands[seg] += len(detections)
                for d in detections:
                    gname = d.gesture_result.gesture.name
                    seg_gestures[seg][gname] = seg_gestures[seg].get(gname, 0) + 1

                if seg == 7:
                    seg7_total_frames += 1
                    if len(detections) >= 2:
                        seg7_both_hands_frames += 1

                event_time = time.time()
                assignments = tracker.assign(detections, event_time)
                for det_i, trk_id in assignments.items():
                    det = detections[det_i]
                    trk = tracker.tracks[trk_id]
                    tracker.update_position(trk, det, event_time)

                    action = state_machine.update(trk, det, event_time)
                    if action != SlideAction.NONE:
                        t_pre_dispatch = time.perf_counter()
                        gesture_to_action_start_ms.append((t_pre_dispatch - t_infer_end) * 1000.0)

                        t_act_start = time.perf_counter()
                        disp = dispatcher.dispatch(action, event_time)
                        t_act_end = time.perf_counter()
                        action_dispatch_duration_ms.append((t_act_end - t_act_start) * 1000.0)

                        if disp:
                            state_machine.latch(trk)
                            seg_actions[seg].append(action.name)
                            capture_to_action_ms.append((t_act_end - t_cap) * 1000.0)

                tracker.expire_lost_tracks(assignments.values(), event_time)
                t_gest_end = time.perf_counter()

                cb_to_gest = (t_gest_end - t_infer_end) * 1000.0
                cap_to_gest = (t_gest_end - t_cap) * 1000.0
                callback_to_gesture_ms.append(cb_to_gest)
                capture_to_gesture_ms.append(cap_to_gest)

            if frame_idx % 20 == 0:
                cpu_samples.append(proc.cpu_percent(interval=None))
                ram_samples.append(proc.memory_info().rss / (1024 * 1024))

            # 30 FPS pacing
            elapsed_in_tick = time.perf_counter() - loop_tick_start
            if elapsed_in_tick < target_frame_interval:
                time.sleep(target_frame_interval - elapsed_in_tick)

            frame_idx += 1

        # Teardown
        if is_worker_mode and control_thread is not None:
            time.sleep(0.1)
            stop_event.set()
            new_result_event.set()
            control_thread.join(timeout=3.0)
            if control_thread.is_alive():
                print("CRITICAL: Control worker thread failed to join!", file=sys.stderr)
        elif is_live:
            time.sleep(0.3)

    wall_end = time.perf_counter()
    cap.release()

    total_wall_s = wall_end - wall_start
    ram_end_mb = proc.memory_info().rss / (1024 * 1024)

    captured_fps = captured_count / total_wall_s if total_wall_s > 0 else 0.0
    submitted_fps = submitted_count / total_wall_s if total_wall_s > 0 else 0.0
    callback_fps = callback_count / total_wall_s if total_wall_s > 0 else 0.0
    processed_fps = processed_count / total_wall_s if total_wall_s > 0 else 0.0

    two_hand_rate_pct = (seg7_both_hands_frames / seg7_total_frames * 100.0) if seg7_total_frames > 0 else 0.0

    results_data = {
        "mode": mode.upper(),
        "wall_time_s": total_wall_s,
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
            "captured_fps": captured_fps,
            "submitted_fps": submitted_fps,
            "callback_fps": callback_fps,
            "processed_fps": processed_fps,
        },
        "drop_rates": {
            "mediapipe_drop_pct": (mediapipe_input_drop / submitted_count * 100.0) if submitted_count > 0 else 0.0,
            "control_overwrite_pct": (control_overwrite_drop / callback_count * 100.0) if callback_count > 0 else 0.0,
            "total_drop_pct": ((mediapipe_input_drop + control_overwrite_drop) / submitted_count * 100.0) if submitted_count > 0 else 0.0,
        },
        "latencies": {
            "capture_to_submit_ms": compute_stats(capture_to_submit_ms),
            "submit_to_callback_ms": compute_stats(submit_to_callback_ms),
            "capture_to_callback_ms": compute_stats(capture_to_callback_ms),
            "callback_to_gesture_ms": compute_stats(callback_to_gesture_ms),
            "capture_to_gesture_ms": compute_stats(capture_to_gesture_ms),
            "gesture_to_action_start_ms": compute_stats(gesture_to_action_start_ms),
            "action_dispatch_duration_ms": compute_stats(action_dispatch_duration_ms),
            "capture_to_action_ms": compute_stats(capture_to_action_ms),
        },
        "system": {
            "ram_start_mb": ram_start_mb,
            "ram_end_mb": ram_end_mb,
            "ram_peak_mb": max(ram_samples) if ram_samples else ram_end_mb,
            "ram_growth_mb": ram_end_mb - ram_start_mb,
            "cpu_mean_pct": float(np.mean(cpu_samples)) if cpu_samples else 0.0,
            "cpu_p95_pct": float(np.percentile(cpu_samples, 95)) if cpu_samples else 0.0,
        },
        "accuracy": {
            "seg7_both_hands_frames": seg7_both_hands_frames,
            "seg7_total_frames": seg7_total_frames,
            "two_hand_detection_rate_pct": round(two_hand_rate_pct, 1),
            "actions_dispatched": seg_actions,
        },
    }

    print(f"Results for {mode.upper()}:")
    print(f"- Submitted: {submitted_count}, Callbacks: {callback_count}, Processed: {processed_count}")
    print(f"- Drops: MP Input Drops={mediapipe_input_drop}, Control Overwrites={control_overwrite_drop}, Evictions={metadata_eviction}")
    print(f"- Submit->Callback Mean: {results_data['latencies']['submit_to_callback_ms']['mean']:.2f} ms")
    print(f"- Callback->Gesture Mean: {results_data['latencies']['callback_to_gesture_ms']['mean']:.2f} ms (P95: {results_data['latencies']['callback_to_gesture_ms']['p95']:.2f} ms)")
    print(f"- Capture->Gesture Mean: {results_data['latencies']['capture_to_gesture_ms']['mean']:.2f} ms")
    print(f"- Capture->Action Mean: {results_data['latencies']['capture_to_action_ms']['mean']:.2f} ms")
    print(f"- Two-hand simultaneous detection rate: {two_hand_rate_pct:.1f}%")
    print(f"- Actions dispatched: {seg_actions}")

    return results_data, frame_csv_rows


def main() -> None:
    modes = ["video", "live_stream_polling", "live_stream_worker"]
    all_results = {}

    for m in modes:
        res, csv_rows = run_single_benchmark(m)
        all_results[m] = res
        if m == "video":
            with open("async_before.json", "w", encoding="utf-8") as f:
                json.dump(res, f, indent=2)
        elif m == "live_stream_polling":
            with open("async_polling.json", "w", encoding="utf-8") as f:
                json.dump(res, f, indent=2)
        elif m == "live_stream_worker":
            with open("async_after.json", "w", encoding="utf-8") as f:
                json.dump(res, f, indent=2)
            if csv_rows:
                with open("async_frame_metrics.csv", "w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=list(csv_rows[0].keys()))
                    writer.writeheader()
                    writer.writerows(csv_rows)

    with open("async_3way_comparison.json", "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2)
    print("\nSaved async_3way_comparison.json successfully!")


if __name__ == "__main__":
    main()
