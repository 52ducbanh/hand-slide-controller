"""Run Stability and Stress Test for LIVE_STREAM Mode.

Tests:
- 1,200+ continuous frames
- Memory growth and memory leak detection
- CPU utilization
- Zero unhandled exceptions
- Clean shutdown
- cProfile execution
Exports:
- async_stability.json
- profile_async.txt
"""

from __future__ import annotations
import cProfile
import json
import os
import pstats
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
from hand_controller.models import SlideAction

_BaseOptions = mp.tasks.BaseOptions
_HandLandmarker = mp.tasks.vision.HandLandmarker
_HandLandmarkerOptions = mp.tasks.vision.HandLandmarkerOptions
_RunningMode = mp.tasks.vision.RunningMode


class _FrameRecord(NamedTuple):
    frame_idx: int
    capture_time: float
    submit_time: float
    timestamp_ms: int


def run_stability_test(target_frames: int = 1200, video_path: str = "benchmark_input.mp4") -> dict[str, Any]:
    print(f"\n=======================================================")
    print(f"Running Stability & Stress Test ({target_frames} frames, LIVE_STREAM)")
    print(f"=======================================================")

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
    callback_count = 0
    callback_errors = 0
    pending_records: dict[int, _FrameRecord] = {}

    def on_async_result(result, image, timestamp_ms: int) -> None:
        nonlocal latest_cb_ts, callback_count, callback_errors
        try:
            cb_time = time.perf_counter()
            with async_lock:
                to_drop = [k for k in pending_records if k < timestamp_ms]
                for k in to_drop:
                    del pending_records[k]

                async_results[timestamp_ms] = (result, cb_time)
                latest_cb_ts = timestamp_ms
                callback_count += 1
        except Exception:
            callback_errors += 1

    options = _HandLandmarkerOptions(
        base_options=_BaseOptions(model_asset_path=cfg.model_path),
        running_mode=_RunningMode.LIVE_STREAM,
        num_hands=cfg.num_hands,
        min_hand_detection_confidence=cfg.min_hand_detection_confidence,
        min_hand_presence_confidence=cfg.min_hand_presence_confidence,
        min_tracking_confidence=cfg.min_tracking_confidence,
        result_callback=on_async_result,
    )

    proc = psutil.Process(os.getpid())
    ram_start_mb = proc.memory_info().rss / (1024 * 1024)
    ram_samples = [ram_start_mb]
    cpu_samples = []

    profiler = cProfile.Profile()
    profiler.enable()

    frame_count = 0
    submitted_count = 0
    processed_count = 0
    actions_fired = 0
    last_ts_ms = -1
    last_processed_ts_ms = -1
    target_frame_interval = 1.0 / 30.0

    wall_start = time.perf_counter()

    with _HandLandmarker.create_from_options(options) as landmarker:
        rgb_buf = None

        while frame_count < target_frames:
            loop_tick_start = time.perf_counter()
            t_cap = time.perf_counter()
            ret, frame = cap.read()
            if not ret:
                # Loop video seamlessly to reach target frame count
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ret, frame = cap.read()
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

            t_submit = time.perf_counter()
            rec = _FrameRecord(frame_count, t_cap, t_submit, ts_ms)
            with async_lock:
                if len(pending_records) >= 120:
                    oldest = sorted(pending_records.keys())[:30]
                    for k in oldest:
                        del pending_records[k]
                pending_records[ts_ms] = rec

            landmarker.detect_async(mp_image, ts_ms)
            submitted_count += 1

            # Check for new result
            res_obj = None
            frame_meta = None
            with async_lock:
                if latest_cb_ts > last_processed_ts_ms:
                    res_obj, cb_time = async_results.pop(latest_cb_ts, (None, None))
                    res_ts = latest_cb_ts
                    frame_meta = pending_records.pop(res_ts, None)

            if res_obj is not None:
                last_processed_ts_ms = res_ts
                processed_count += 1
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
                for det_i, trk_id in assignments.items():
                    det = detections[det_i]
                    trk = tracker.tracks[trk_id]
                    tracker.update_position(trk, det, event_time)

                    action = state_machine.update(trk, det, event_time)
                    if action != SlideAction.NONE:
                        disp = dispatcher.dispatch(action, event_time)
                        if disp:
                            state_machine.latch(trk)
                            actions_fired += 1

                tracker.expire_lost_tracks(assignments.values(), event_time)

            elapsed_in_tick = time.perf_counter() - loop_tick_start
            if elapsed_in_tick < target_frame_interval:
                time.sleep(target_frame_interval - elapsed_in_tick)

            frame_count += 1
            if frame_count % 50 == 0:
                cur_ram = proc.memory_info().rss / (1024 * 1024)
                ram_samples.append(cur_ram)
                cpu_samples.append(proc.cpu_percent(interval=None))

        time.sleep(0.3)

    profiler.disable()
    wall_end = time.perf_counter()
    cap.release()

    # Save cProfile output
    with open("profile_async.txt", "w", encoding="utf-8") as f:
        ps = pstats.Stats(profiler, stream=f).sort_stats("cumulative")
        f.write("=== TOP 30 BY CUMULATIVE TIME (LIVE_STREAM) ===\n")
        ps.print_stats(30)
        f.write("\n\n=== TOP 30 BY TOTAL (INTERNAL) TIME (LIVE_STREAM) ===\n")
        ps.sort_stats("time")
        ps.print_stats(30)
    print("Exported profile_async.txt")

    total_time_s = wall_end - wall_start
    ram_end_mb = proc.memory_info().rss / (1024 * 1024)
    ram_peak_mb = max(ram_samples)
    ram_growth_mb = ram_end_mb - ram_start_mb

    # Repeated Startup / Teardown Test
    repeated_startups = []
    for cycle in range(1, 4):
        t0 = time.perf_counter()
        c = cv2.VideoCapture(video_path)
        t_cam = time.perf_counter()
        opts = _HandLandmarkerOptions(
            base_options=_BaseOptions(model_asset_path=cfg.model_path),
            running_mode=_RunningMode.LIVE_STREAM,
            num_hands=cfg.num_hands,
            result_callback=lambda res, img, ts: None,
        )
        with _HandLandmarker.create_from_options(opts) as lm:
            t_model = time.perf_counter()
            ret, fr = c.read()
            t_first = time.perf_counter()
        c.release()
        repeated_startups.append({
            "cycle": cycle,
            "success": ret,
            "cam_init_ms": (t_cam - t0) * 1000,
            "model_init_ms": (t_model - t_cam) * 1000,
            "first_frame_ms": (t_first - t_model) * 1000,
            "total_ms": (t_first - t0) * 1000,
        })

    stability_data = {
        "frames_processed": frame_count,
        "frames_submitted": submitted_count,
        "callbacks_received": callback_count,
        "results_processed": processed_count,
        "total_time_s": total_time_s,
        "throughput_fps": frame_count / total_time_s if total_time_s > 0 else 0.0,
        "actions_fired": actions_fired,
        "callback_errors": callback_errors,
        "ram": {
            "start_mb": ram_start_mb,
            "end_mb": ram_end_mb,
            "peak_mb": ram_peak_mb,
            "growth_mb": ram_growth_mb,
            "leak_detected": bool(ram_growth_mb > 35.0),
        },
        "cpu": {
            "mean_pct": float(np.mean(cpu_samples)) if cpu_samples else 0.0,
            "p95_pct": float(np.percentile(cpu_samples, 95)) if cpu_samples else 0.0,
        },
        "repeated_startups": repeated_startups,
        "verdict": "PASS" if (callback_errors == 0 and ram_growth_mb <= 35.0) else "FAIL",
    }

    print(f"\nStability Test Summary:")
    print(f"Frames: {frame_count}, Wall Time: {total_time_s:.2f}s, Throughput: {stability_data['throughput_fps']:.2f} FPS")
    print(f"RAM: Start={ram_start_mb:.1f}MB, End={ram_end_mb:.1f}MB, Peak={ram_peak_mb:.1f}MB, Growth={ram_growth_mb:.2f}MB (Leak={stability_data['ram']['leak_detected']})")
    print(f"Callback Errors: {callback_errors}")
    print(f"Repeated Startups (3 cycles): All successful = {all(s['success'] for s in repeated_startups)}")
    print(f"Stability Verdict: {stability_data['verdict']}")

    return stability_data


def main() -> None:
    results = run_stability_test(target_frames=1200)
    with open("async_stability.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print("Exported async_stability.json")


if __name__ == "__main__":
    main()
