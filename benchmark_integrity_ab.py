"""Final Benchmark Integrity Harness for Hand Slide Controller.

Strictly compares Condition A (NORMAL_PRIORITY_CLASS) vs Condition B (ABOVE_NORMAL_PRIORITY_CLASS)
under identical, standardized conditions:
- Same input: benchmark_input.mp4 (800 frames, 1280x720, 30 FPS pacing)
- Same model: hand_landmarker.task (num_hands=2, det=0.25, pres=0.25, trk=0.25)
- Same architecture: LIVE_STREAM_WORKER with event-driven Control Worker
- Strict process isolation: each trial runs in a dedicated clean subprocess
- Defined warmup: 60 frames per trial, measurements discarded before benchmark
- Strict monotonic clock domain: time.perf_counter() for all latency calculations
- Action latency recorded strictly on real action dispatches
- Explicitly tracks Submit->Callback and all pipeline stages
- Exports: benchmark_integrity_ab.json, benchmark_integrity_trials.csv
"""

from __future__ import annotations
import argparse
import csv
import json
import os
import subprocess
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


def run_worker_trial(
    condition: str,
    trial_id: int,
    warmup_frames: int = 60,
    video_path: str = "benchmark_input.mp4",
) -> dict[str, Any]:
    proc = psutil.Process(os.getpid())
    if condition == "ABOVE_NORMAL":
        prio = psutil.ABOVE_NORMAL_PRIORITY_CLASS
    else:
        prio = psutil.NORMAL_PRIORITY_CLASS

    try:
        proc.nice(prio)
    except Exception as e:
        print(f"Warning: Could not set priority: {e}", file=sys.stderr)

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

    shared_lock = threading.Lock()
    new_result_event = threading.Event()
    stop_event = threading.Event()
    latest_worker_packet: _WorkerPacket | None = None
    pending_records: dict[int, _FrameRecord] = {}

    callback_count = 0
    submitted_count = 0
    captured_count = 0
    processed_count = 0
    mediapipe_input_drop = 0
    control_overwrite_drop = 0
    metadata_eviction = 0

    capture_to_submit_ms: list[float] = []
    submit_to_callback_ms: list[float] = []
    callback_to_gesture_ms: list[float] = []
    capture_to_gesture_ms: list[float] = []
    gesture_done_to_action_start_ms: list[float] = []
    action_dispatch_duration_ms: list[float] = []
    capture_to_action_ms: list[float] = []
    real_actions_recorded: list[dict[str, Any]] = []

    cpu_samples: list[float] = []
    ram_samples: list[float] = []
    seg7_both_hands_frames = 0
    seg7_total_frames = 0
    is_measuring = False

    def on_async_result(result, image, timestamp_ms: int) -> None:
        nonlocal callback_count, mediapipe_input_drop, control_overwrite_drop, latest_worker_packet
        cb_time = time.perf_counter()
        with shared_lock:
            if stop_event.is_set():
                return
            older = [k for k in pending_records if k < timestamp_ms]
            for k in older:
                del pending_records[k]
                if is_measuring:
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

            if latest_worker_packet is not None and is_measuring:
                control_overwrite_drop += 1

            latest_worker_packet = packet
            if is_measuring:
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
            if not is_measuring:
                continue

            processed_count += 1
            t_gest_start = time.perf_counter()

            sub_to_cb = (packet.callback_time - packet.submit_time) * 1000.0
            submit_to_callback_ms.append(sub_to_cb)

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

            for det_i, trk_id in assignments.items():
                det = detections[det_i]
                trk = tracker.tracks[trk_id]
                tracker.update_position(trk, det, event_time)
                action = state_machine.update(trk, det, event_time)
                if action != SlideAction.NONE:
                    t_pre_disp = time.perf_counter()
                    g_done_to_act = (t_pre_disp - t_gest_start) * 1000.0
                    gesture_done_to_action_start_ms.append(g_done_to_act)

                    t_act_start = time.perf_counter()
                    disp = dispatcher.dispatch(action, event_time)
                    t_act_end = time.perf_counter()
                    act_dur = (t_act_end - t_act_start) * 1000.0
                    action_dispatch_duration_ms.append(act_dur)

                    if disp:
                        state_machine.latch(trk)
                        c2a = (t_act_end - packet.capture_time) * 1000.0
                        capture_to_action_ms.append(c2a)
                        real_actions_recorded.append({
                            "action": action.name,
                            "frame_idx": packet.frame_idx,
                            "seg_idx": packet.seg_idx,
                            "capture_time": packet.capture_time,
                            "dispatch_start": t_act_start,
                            "dispatch_end": t_act_end,
                            "capture_to_action_ms": round(c2a, 3),
                        })

            tracker.expire_lost_tracks(assignments.values(), event_time)
            t_gest_end = time.perf_counter()

            cb_to_gest = (t_gest_end - packet.callback_time) * 1000.0
            cap_to_gest = (t_gest_end - packet.capture_time) * 1000.0
            callback_to_gesture_ms.append(cb_to_gest)
            capture_to_gesture_ms.append(cap_to_gest)

    control_thread = threading.Thread(target=worker_loop, name=f"IntegrityWorker_{trial_id}", daemon=False)
    control_thread.start()

    with _HandLandmarker.create_from_options(options) as landmarker:
        # ==========================================
        # PHASE 1: DEFINED WARMUP (60 frames, discarded)
        # ==========================================
        cap_warmup = cv2.VideoCapture(video_path)
        if not cap_warmup.isOpened():
            raise RuntimeError(f"Cannot open {video_path}")

        rgb_buf = None
        target_frame_interval = 1.0 / 30.0
        last_ts_ms = -1

        for w_idx in range(warmup_frames):
            t_tick = time.perf_counter()
            ret, frame = cap_warmup.read()
            if not ret:
                break
            cv2.flip(frame, 1, dst=frame)
            if rgb_buf is None or rgb_buf.shape != frame.shape:
                rgb_buf = np.empty_like(frame)
            cv2.cvtColor(frame, cv2.COLOR_BGR2RGB, dst=rgb_buf)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_buf)

            now_ms = time.monotonic_ns() // 1_000_000
            ts_ms = max(now_ms, last_ts_ms + 1)
            last_ts_ms = ts_ms

            t_sub = time.perf_counter()
            rec = _FrameRecord(w_idx, 1, t_tick, t_sub, ts_ms)
            with shared_lock:
                pending_records[ts_ms] = rec
            landmarker.detect_async(mp_image, ts_ms)

            dt = time.perf_counter() - t_tick
            if dt < target_frame_interval:
                time.sleep(target_frame_interval - dt)

        time.sleep(0.3)
        cap_warmup.release()

        # Clear warmup state and start fresh measurement
        with shared_lock:
            pending_records.clear()
            latest_worker_packet = None
        new_result_event.clear()

        # Reset trackers & state machine for measurement run
        tracker.tracks = [tracker.tracks[0].__class__(track_id=0), tracker.tracks[1].__class__(track_id=1)]
        is_measuring = True

        # ==========================================
        # PHASE 2: BENCHMARK MEASUREMENT (800 frames)
        # ==========================================
        cap_bench = cv2.VideoCapture(video_path)
        if not cap_bench.isOpened():
            raise RuntimeError(f"Cannot open {video_path}")

        frame_idx = 0
        wall_start = time.perf_counter()

        while True:
            loop_tick_start = time.perf_counter()
            t_cap = time.perf_counter()
            ret, frame = cap_bench.read()
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

            elapsed = time.perf_counter() - loop_tick_start
            if elapsed < target_frame_interval:
                time.sleep(target_frame_interval - elapsed)
            frame_idx += 1

        time.sleep(0.15)
        stop_event.set()
        new_result_event.set()
        control_thread.join(timeout=3.0)

    wall_end = time.perf_counter()
    cap_bench.release()
    total_wall_s = wall_end - wall_start

    pacing_fps = captured_count / total_wall_s if total_wall_s > 0 else 0.0
    submitted_fps = submitted_count / total_wall_s if total_wall_s > 0 else 0.0
    callback_fps = callback_count / total_wall_s if total_wall_s > 0 else 0.0
    processed_fps = processed_count / total_wall_s if total_wall_s > 0 else 0.0

    two_hand_rate = (seg7_both_hands_frames / seg7_total_frames * 100.0) if seg7_total_frames > 0 else 0.0

    return {
        "trial_id": trial_id,
        "condition": condition,
        "priority_class": prio,
        "wall_time_s": round(total_wall_s, 3),
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
            "pacing_input_fps": round(pacing_fps, 2),
            "submitted_fps": round(submitted_fps, 2),
            "callback_fps": round(callback_fps, 2),
            "processed_fps": round(processed_fps, 2),
        },
        "latencies": {
            "capture_to_submit_ms": compute_stats(capture_to_submit_ms),
            "submit_to_callback_ms": compute_stats(submit_to_callback_ms),
            "callback_to_gesture_ms": compute_stats(callback_to_gesture_ms),
            "capture_to_gesture_ms": compute_stats(capture_to_gesture_ms),
            "gesture_done_to_action_start_ms": compute_stats(gesture_done_to_action_start_ms),
            "action_dispatch_duration_ms": compute_stats(action_dispatch_duration_ms),
            "capture_to_action_ms": compute_stats(capture_to_action_ms),
        },
        "real_actions": {
            "count": len(real_actions_recorded),
            "actions": real_actions_recorded,
        },
        "two_hand_detection_rate_pct": round(two_hand_rate, 2),
        "system": {
            "cpu_mean_pct": round(float(np.mean(cpu_samples)), 2) if cpu_samples else 0.0,
            "cpu_p95_pct": round(float(np.percentile(cpu_samples, 95)), 2) if cpu_samples else 0.0,
            "ram_mean_mb": round(float(np.mean(ram_samples)), 2) if ram_samples else 0.0,
            "ram_peak_mb": round(float(np.max(ram_samples)), 2) if ram_samples else 0.0,
        },
    }


def main():
    parser = argparse.ArgumentParser(description="Final Benchmark Integrity Harness")
    parser.add_argument("--worker", action="store_true", help="Run single isolated worker trial")
    parser.add_argument("--condition", choices=["NORMAL", "ABOVE_NORMAL"], default="NORMAL")
    parser.add_argument("--trial-id", type=int, default=1)
    parser.add_argument("--out-json", type=str, default="")
    args = parser.parse_args()

    if args.worker:
        trial_data = run_worker_trial(
            condition=args.condition,
            trial_id=args.trial_id,
        )
        if args.out_json:
            with open(args.out_json, "w", encoding="utf-8") as f:
                json.dump(trial_data, f, indent=2)
        else:
            print(json.dumps(trial_data))
        return

    # Parent Orchestrator: Runs balanced alternating sequence A, B, A, B, A, B
    order = ["NORMAL", "ABOVE_NORMAL", "NORMAL", "ABOVE_NORMAL", "NORMAL", "ABOVE_NORMAL"]
    print("==================================================================")
    print("STARTING FINAL BENCHMARK INTEGRITY PASS: BALANCED A/B SEQUENCE")
    print(f"Order: {order}")
    print("==================================================================")

    trials_results: list[dict[str, Any]] = []
    trials_csv_rows: list[dict[str, Any]] = []

    for i, cond in enumerate(order, start=1):
        print(f"\n>>> Running Trial {i}/6: Condition = {cond} in clean subprocess...")
        tmp_out = f"tmp_trial_{i}_{cond}.json"
        cmd = [
            sys.executable,
            __file__,
            "--worker",
            "--condition", cond,
            "--trial-id", str(i),
            "--out-json", tmp_out,
        ]
        t_start = time.perf_counter()
        ret = subprocess.run(cmd, capture_output=True, text=True)
        t_elapsed = time.perf_counter() - t_start

        if ret.returncode != 0:
            print(f"Error in trial {i}:\n{ret.stderr}", file=sys.stderr)
            continue

        if not os.path.exists(tmp_out):
            print(f"Error: Missing output file {tmp_out}", file=sys.stderr)
            continue

        with open(tmp_out, "r", encoding="utf-8") as f:
            t_data = json.load(f)
        try:
            os.remove(tmp_out)
        except Exception:
            pass

        trials_results.append(t_data)
        lats = t_data["latencies"]
        fps = t_data["fps"]
        counts = t_data["counts"]
        c2a = lats["capture_to_action_ms"]
        s2cb = lats["submit_to_callback_ms"]

        print(f"Trial {i} ({cond}) Finished in {t_elapsed:.1f}s:")
        print(f"  Processed FPS: {fps['processed_fps']:.2f}, Drops: {counts['mediapipe_input_drops']}")
        print(f"  Submit->Callback: Mean={s2cb['mean']:.2f} ms, Median={s2cb['median']:.2f} ms")
        print(f"  Capture->Action: Mean={c2a['mean']:.2f} ms, Median={c2a['median']:.2f} ms, P95={c2a['p95']:.2f} ms")
        print(f"  Actions Fired: {t_data['real_actions']['count']}, 2-Hand Rate: {t_data['two_hand_detection_rate_pct']:.1f}%")

        trials_csv_rows.append({
            "trial_id": i,
            "condition": cond,
            "wall_time_s": t_data["wall_time_s"],
            "submitted_fps": fps["submitted_fps"],
            "processed_fps": fps["processed_fps"],
            "mediapipe_drops": counts["mediapipe_input_drops"],
            "submit_to_callback_mean_ms": round(s2cb["mean"], 2),
            "submit_to_callback_median_ms": round(s2cb["median"], 2),
            "callback_to_gesture_mean_ms": round(lats["callback_to_gesture_ms"]["mean"], 2),
            "capture_to_action_mean_ms": round(c2a["mean"], 2),
            "capture_to_action_median_ms": round(c2a["median"], 2),
            "capture_to_action_p95_ms": round(c2a["p95"], 2),
            "two_hand_rate_pct": t_data["two_hand_detection_rate_pct"],
            "cpu_mean_pct": t_data["system"]["cpu_mean_pct"],
            "ram_peak_mb": t_data["system"]["ram_peak_mb"],
        })

    # Aggregate by condition
    def aggregate_condition(cond_name: str) -> dict[str, Any]:
        c_trials = [t for t in trials_results if t["condition"] == cond_name]
        if not c_trials:
            return {}

        def get_vals(key_path: list[str]) -> list[float]:
            vals = []
            for t in c_trials:
                cur = t
                for k in key_path:
                    cur = cur[k]
                vals.append(float(cur))
            return vals

        return {
            "trials_count": len(c_trials),
            "processed_fps": {
                "mean": round(float(np.mean(get_vals(["fps", "processed_fps"]))), 2),
                "std": round(float(np.std(get_vals(["fps", "processed_fps"]))), 2),
                "values": get_vals(["fps", "processed_fps"]),
            },
            "submit_to_callback_mean_ms": {
                "mean": round(float(np.mean(get_vals(["latencies", "submit_to_callback_ms", "mean"]))), 2),
                "std": round(float(np.std(get_vals(["latencies", "submit_to_callback_ms", "mean"]))), 2),
                "values": get_vals(["latencies", "submit_to_callback_ms", "mean"]),
            },
            "submit_to_callback_median_ms": {
                "mean": round(float(np.mean(get_vals(["latencies", "submit_to_callback_ms", "median"]))), 2),
                "std": round(float(np.std(get_vals(["latencies", "submit_to_callback_ms", "median"]))), 2),
            },
            "callback_to_gesture_mean_ms": {
                "mean": round(float(np.mean(get_vals(["latencies", "callback_to_gesture_ms", "mean"]))), 2),
                "std": round(float(np.std(get_vals(["latencies", "callback_to_gesture_ms", "mean"]))), 2),
            },
            "capture_to_action_mean_ms": {
                "mean": round(float(np.mean(get_vals(["latencies", "capture_to_action_ms", "mean"]))), 2),
                "std": round(float(np.std(get_vals(["latencies", "capture_to_action_ms", "mean"]))), 2),
                "values": get_vals(["latencies", "capture_to_action_ms", "mean"]),
            },
            "capture_to_action_median_ms": {
                "mean": round(float(np.mean(get_vals(["latencies", "capture_to_action_ms", "median"]))), 2),
                "std": round(float(np.std(get_vals(["latencies", "capture_to_action_ms", "median"]))), 2),
                "values": get_vals(["latencies", "capture_to_action_ms", "median"]),
            },
            "capture_to_action_p95_ms": {
                "mean": round(float(np.mean(get_vals(["latencies", "capture_to_action_ms", "p95"]))), 2),
                "std": round(float(np.std(get_vals(["latencies", "capture_to_action_ms", "p95"]))), 2),
                "values": get_vals(["latencies", "capture_to_action_ms", "p95"]),
            },
            "mediapipe_input_drops": {
                "mean": round(float(np.mean(get_vals(["counts", "mediapipe_input_drops"]))), 1),
                "values": get_vals(["counts", "mediapipe_input_drops"]),
            },
            "two_hand_detection_rate_pct": {
                "mean": round(float(np.mean(get_vals(["two_hand_detection_rate_pct"]))), 2),
                "std": round(float(np.std(get_vals(["two_hand_detection_rate_pct"]))), 2),
            },
            "cpu_mean_pct": {
                "mean": round(float(np.mean(get_vals(["system", "cpu_mean_pct"]))), 2),
            },
        }

    normal_summary = aggregate_condition("NORMAL")
    above_summary = aggregate_condition("ABOVE_NORMAL")

    final_payload = {
        "metadata": {
            "test_type": "FINAL_BENCHMARK_INTEGRITY_AB",
            "date": time.strftime("%Y-%m-%d %H:%M:%S"),
            "video_input": "benchmark_input.mp4 (800 frames, 1280x720, 30 FPS pacing)",
            "warmup_frames_per_trial": 60,
            "trials_order": order,
            "clock_domain": "time.perf_counter() monotonic wall clock",
        },
        "condition_normal": normal_summary,
        "condition_above_normal": above_summary,
        "comparison": {
            "processed_fps_delta": round(above_summary["processed_fps"]["mean"] - normal_summary["processed_fps"]["mean"], 2),
            "submit_to_callback_mean_delta_ms": round(above_summary["submit_to_callback_mean_ms"]["mean"] - normal_summary["submit_to_callback_mean_ms"]["mean"], 2),
            "capture_to_action_mean_delta_ms": round(above_summary["capture_to_action_mean_ms"]["mean"] - normal_summary["capture_to_action_mean_ms"]["mean"], 2),
            "capture_to_action_p95_delta_ms": round(above_summary["capture_to_action_p95_ms"]["mean"] - normal_summary["capture_to_action_p95_ms"]["mean"], 2),
        },
        "trials": trials_results,
    }

    with open("benchmark_integrity_ab.json", "w", encoding="utf-8") as f:
        json.dump(final_payload, f, indent=2)

    if trials_csv_rows:
        with open("benchmark_integrity_trials.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(trials_csv_rows[0].keys()))
            writer.writeheader()
            writer.writerows(trials_csv_rows)

    print("\n==================================================================")
    print("FINAL BENCHMARK INTEGRITY COMPLETE")
    print("Saved benchmark_integrity_ab.json and benchmark_integrity_trials.csv")
    print("==================================================================")


if __name__ == "__main__":
    main()
