"""Production v2: 10-Minute Continuous Stability & Memory Leak Stress Test.

Duration: 600s (10 minutes)
Priority: ABOVE_NORMAL
Monitors:
- RSS and CPU sampled every 5s
- Warmup (0-60s) excluded from steady-state slope
- Callback count, processed results, drops, actions, errors
- Clean worker shutdown & join verification
Exports:
- production_v2_stability.json
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


class _WorkerPacket(NamedTuple):
    result: Any
    timestamp_ms: int
    capture_time: float
    callback_time: float


def run_production_v2_stability(duration_seconds: float = 600.0, video_path: str = "benchmark_input.mp4") -> dict[str, Any]:
    print(f"\n=======================================================")
    print(f"STARTING PRODUCTION V2 10-MINUTE STABILITY TEST ({duration_seconds:.0f}s)")
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

    shared_lock = threading.Lock()
    new_result_event = threading.Event()
    stop_event = threading.Event()
    latest_packet: _WorkerPacket | None = None
    pending_meta: dict[int, float] = {}

    callback_count = 0
    submitted_count = 0
    processed_count = 0
    actions_fired = 0
    callback_errors = 0
    thread_errors = 0

    def on_async_result(result, image, timestamp_ms: int) -> None:
        nonlocal callback_count, callback_errors, latest_packet
        try:
            cb_time = time.perf_counter()
            with shared_lock:
                if stop_event.is_set():
                    return
                for k in [k for k in pending_meta if k < timestamp_ms]:
                    del pending_meta[k]

                cap_time = pending_meta.pop(timestamp_ms, cb_time)
                latest_packet = _WorkerPacket(result, timestamp_ms, cap_time, cb_time)
                callback_count += 1
            new_result_event.set()
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

    def worker_loop() -> None:
        nonlocal processed_count, actions_fired, thread_errors, latest_packet
        last_processed_ms = -1
        try:
            while not stop_event.is_set():
                signaled = new_result_event.wait(timeout=0.05)
                if stop_event.is_set():
                    break
                if not signaled:
                    continue

                pkt: _WorkerPacket | None = None
                with shared_lock:
                    if latest_packet is not None and latest_packet.timestamp_ms > last_processed_ms:
                        pkt = latest_packet
                        latest_packet = None
                    new_result_event.clear()

                if pkt is None:
                    continue

                last_processed_ms = pkt.timestamp_ms
                processed_count += 1

                detections = []
                if pkt.result.hand_landmarks:
                    for i, lms in enumerate(pkt.result.hand_landmarks):
                        lbl = "Unknown"
                        score = 0.0
                        if pkt.result.handedness and i < len(pkt.result.handedness) and pkt.result.handedness[i]:
                            lbl = pkt.result.handedness[i][0].category_name
                            score = pkt.result.handedness[i][0].score
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

                event_time = pkt.capture_time
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
        except Exception:
            thread_errors += 1

    control_thread = threading.Thread(target=worker_loop, name="ProdV2StabilityWorker", daemon=False)
    control_thread.start()

    samples = []
    t_start = time.perf_counter()
    last_sample_t = t_start
    target_frame_interval = 1.0 / 30.0
    last_ts_ms = -1
    rgb_buf = None

    with _HandLandmarker.create_from_options(options) as landmarker:
        while True:
            loop_tick_start = time.perf_counter()
            elapsed_total = loop_tick_start - t_start
            if elapsed_total >= duration_seconds:
                break

            ret, frame = cap.read()
            if not ret:
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

            with shared_lock:
                if len(pending_meta) >= 120:
                    for k in sorted(pending_meta.keys())[:30]:
                        del pending_meta[k]
                pending_meta[ts_ms] = loop_tick_start

            landmarker.detect_async(mp_image, ts_ms)
            submitted_count += 1

            if loop_tick_start - last_sample_t >= 5.0:
                cur_ram = proc.memory_info().rss / (1024 * 1024)
                cur_cpu = proc.cpu_percent(interval=None)
                samples.append({
                    "elapsed_s": round(elapsed_total, 1),
                    "ram_mb": round(cur_ram, 2),
                    "cpu_pct": round(cur_cpu, 1),
                })
                last_sample_t = loop_tick_start
                if len(samples) % 12 == 0:
                    print(f"[{elapsed_total/60.0:.1f} min] RAM: {cur_ram:.1f} MB, CPU: {cur_cpu:.1f}%, Callbacks: {callback_count}")

            elapsed_tick = time.perf_counter() - loop_tick_start
            if elapsed_tick < target_frame_interval:
                time.sleep(target_frame_interval - elapsed_tick)

        time.sleep(0.1)
        stop_event.set()
        new_result_event.set()
        control_thread.join(timeout=3.0)
        worker_alive = control_thread.is_alive()

    t_end = time.perf_counter()
    cap.release()
    total_actual_s = t_end - t_start

    # Steady-state memory audit (60s to 600s)
    steady_samples = [s for s in samples if s["elapsed_s"] >= 60.0]

    def get_closest_ram(target_s: float) -> float:
        if not samples:
            return 0.0
        return min(samples, key=lambda s: abs(s["elapsed_s"] - target_s))["ram_mb"]

    rss_60s = get_closest_ram(60.0)
    rss_300s = get_closest_ram(300.0)
    rss_600s = get_closest_ram(duration_seconds)

    if len(steady_samples) >= 2:
        t_mins = np.array([s["elapsed_s"] / 60.0 for s in steady_samples])
        rams = np.array([s["ram_mb"] for s in steady_samples])
        slope_mb_per_min, intercept = np.polyfit(t_mins, rams, 1)
        steady_min_mb = float(np.min(rams))
        steady_max_mb = float(np.max(rams))
    else:
        slope_mb_per_min = 0.0
        steady_min_mb = rss_60s
        steady_max_mb = rss_600s

    leak_detected = bool(slope_mb_per_min > 1.5)

    stability_summary = {
        "duration_requested_s": duration_seconds,
        "duration_actual_s": round(total_actual_s, 2),
        "total_frames_submitted": submitted_count,
        "total_callbacks_received": callback_count,
        "total_results_processed": processed_count,
        "actions_fired": actions_fired,
        "callback_errors": callback_errors,
        "thread_errors": thread_errors,
        "worker_thread_alive_after_join": worker_alive,
        "throughput_fps": round(submitted_count / total_actual_s, 2),
        "memory_audit": {
            "warmup_duration_s": 60.0,
            "steady_state_duration_s": round(total_actual_s - 60.0, 2),
            "rss_at_60s_mb": rss_60s,
            "rss_at_300s_mb": rss_300s,
            "rss_at_600s_mb": rss_600s,
            "steady_state_min_mb": round(steady_min_mb, 2),
            "steady_state_max_mb": round(steady_max_mb, 2),
            "steady_state_slope_mb_per_min": round(slope_mb_per_min, 4),
            "memory_plateau_confirmed": not leak_detected,
            "leak_detected": leak_detected,
        },
        "samples_every_5s": samples,
        "verdict": "PASS" if (callback_errors == 0 and thread_errors == 0 and not worker_alive and not leak_detected) else "FAIL",
    }

    with open("production_v2_stability.json", "w", encoding="utf-8") as f:
        json.dump(stability_summary, f, indent=2)

    print(f"\nProduction v2 Stability Results:")
    print(f"- Submitted: {submitted_count} frames ({submitted_count/total_actual_s:.1f} FPS)")
    print(f"- Processed: {processed_count}, Actions: {actions_fired}, Errors: {callback_errors + thread_errors}")
    print(f"- Worker cleanly terminated: {not worker_alive}")
    print(f"- RSS @ 60s: {rss_60s:.1f} MB, @ 300s: {rss_300s:.1f} MB, @ 600s: {rss_600s:.1f} MB")
    print(f"- Steady-state Slope: {slope_mb_per_min:.4f} MB/min (Plateau = {not leak_detected})")
    print(f"- Verdict: {stability_summary['verdict']}")
    return stability_summary


if __name__ == "__main__":
    dur = float(sys.argv[1]) if len(sys.argv) > 1 else 600.0
    run_production_v2_stability(dur)
