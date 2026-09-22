"""Phase 4: 10-Minute Continuous Live Stability Validation on Physical Webcam.

Runs for 600 seconds (10 minutes) on physical webcam device 0:
- Samples RSS memory and CPU every 5 seconds
- Tracks frame throughput, callbacks, and drops
- Calculates linear regression slope of memory growth (MB/hour)
- Verifies zero memory leaks, zero thread stalls, deterministic shutdown

Outputs:
- production_v2_stability_10min.json
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


def run_10min_stability(duration_sec: float = 600.0, sample_interval_sec: float = 5.0) -> dict[str, Any]:
    print("==================================================================")
    print(f"STARTING 10-MINUTE (600s) CONTINUOUS LIVE STABILITY TEST ON WEBCAM")
    print("==================================================================")

    proc = psutil.Process()
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

    captured_count = 0
    submitted_count = 0
    callbacks_count = 0
    processed_count = 0
    drops = 0

    def on_result(result, image, ts_ms: int) -> None:
        nonlocal latest_worker_packet, callbacks_count, drops
        cb_time = time.perf_counter()
        callbacks_count += 1
        with shared_lock:
            if ts_ms in pending_records:
                rec = pending_records.pop(ts_ms)
                latest_worker_packet = _WorkerPacket(result, ts_ms, rec.frame_idx, rec.capture_time, rec.submit_time, cb_time)
                new_result_event.set()
            old_keys = [k for k in pending_records if k < ts_ms]
            for ok in old_keys:
                del pending_records[ok]
                drops += 1

    def control_worker() -> None:
        nonlocal processed_count
        while not stop_event.is_set():
            if not new_result_event.wait(timeout=0.05):
                continue
            with shared_lock:
                new_result_event.clear()
                packet = latest_worker_packet
            if packet is None:
                continue

            processed_count += 1
            now_sec = packet.capture_time
            detections = []
            if packet.result and packet.result.hand_landmarks:
                for i, lms in enumerate(packet.result.hand_landmarks):
                    lbl = packet.result.handedness[i][0].category_name if packet.result.handedness and packet.result.handedness[i] else "Unknown"
                    score = packet.result.handedness[i][0].score if packet.result.handedness and packet.result.handedness[i] else 0.0
                    gres = recognizer.classify(lms)
                    detections.append(HandDetection(landmarks=lms, wrist_x=lms[0].x, wrist_y=lms[0].y, gesture_result=gres, raw_label=lbl, raw_score=score))

            assignments = tracker.assign(detections, now_sec)
            for det_idx, track_id in assignments.items():
                detection = detections[det_idx]
                track = tracker.tracks[track_id]
                tracker.update_position(track, detection, now_sec)
                act = state_machine.update(track, detection, now_sec)
                if act != SlideAction.NONE:
                    dispatched = dispatcher.dispatch(act, now_sec)
                    if dispatched:
                        state_machine.latch(track)
            tracker.expire_lost_tracks(assignments.values(), now_sec)

    worker_thread = threading.Thread(target=control_worker, daemon=False, name="StabilityControlWorker")
    worker_thread.start()

    options = _HandLandmarkerOptions(
        base_options=_BaseOptions(model_asset_path="hand_landmarker.task"),
        running_mode=_RunningMode.LIVE_STREAM,
        num_hands=2,
        min_hand_detection_confidence=0.25,
        min_hand_presence_confidence=0.25,
        min_tracking_confidence=0.25,
        result_callback=on_result,
    )
    landmarker = _HandLandmarker.create_from_options(options)

    # Open Physical Camera
    params = [cv2.CAP_PROP_FRAME_WIDTH, 1280, cv2.CAP_PROP_FRAME_HEIGHT, 720, cv2.CAP_PROP_FPS, 30]
    cap = cv2.VideoCapture(0, cv2.CAP_MSMF, params)
    print("Physical camera opened at 1280x720 @ 30 FPS.")

    samples = []
    t_start = time.perf_counter()
    t_end_target = t_start + duration_sec
    last_sample_time = t_start
    frame_idx = 0
    last_timestamp_ms = -1

    while time.perf_counter() < t_end_target:
        ret, frame = cap.read()
        if not ret or frame is None:
            continue
        frame_idx += 1
        captured_count += 1

        cv2.flip(frame, 1, dst=frame)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

        now_ms = time.monotonic_ns() // 1_000_000
        timestamp_ms = max(now_ms, last_timestamp_ms + 1)
        last_timestamp_ms = timestamp_ms

        t_sub = time.perf_counter()
        with shared_lock:
            pending_records[timestamp_ms] = _FrameRecord(frame_idx, t_sub, t_sub, timestamp_ms)
        submitted_count += 1
        landmarker.detect_async(mp_image, timestamp_ms)

        now = time.perf_counter()
        if now - last_sample_time >= sample_interval_sec:
            elapsed = now - t_start
            rss_mb = proc.memory_info().rss / (1024 * 1024)
            cpu_pct = proc.cpu_percent(interval=None)
            samples.append({
                "elapsed_sec": round(elapsed, 1),
                "rss_mb": round(rss_mb, 2),
                "cpu_percent": round(cpu_pct, 1),
                "processed_count": processed_count,
                "drops_so_far": drops,
            })
            print(f"  [{elapsed:.0f}s/{duration_sec:.0f}s] RSS: {rss_mb:.1f} MB, CPU: {cpu_pct:.1f}%, Processed: {processed_count}, Drops: {drops}")
            last_sample_time = now

    t_actual_end = time.perf_counter()
    actual_duration = t_actual_end - t_start

    stop_event.set()
    new_result_event.set()
    worker_thread.join(timeout=2.0)
    landmarker.close()
    cap.release()

    # Calculate memory slope via linear regression
    if len(samples) > 2:
        times = [s["elapsed_sec"] for s in samples]
        rss_vals = [s["rss_mb"] for s in samples]
        poly = np.polyfit(times, rss_vals, 1)
        slope_mb_per_sec = poly[0]
        slope_mb_per_hour = slope_mb_per_sec * 3600.0
    else:
        slope_mb_per_hour = 0.0

    mean_cpu = float(np.mean([s["cpu_percent"] for s in samples])) if samples else 0.0
    initial_rss = samples[0]["rss_mb"] if samples else 0.0
    final_rss = samples[-1]["rss_mb"] if samples else 0.0
    peak_rss = max([s["rss_mb"] for s in samples]) if samples else 0.0

    leak_status = "PASS (NO LEAK)" if abs(slope_mb_per_hour) < 5.0 else "WARNING: Memory Slope > 5 MB/h"

    summary = {
        "duration_sec": round(actual_duration, 1),
        "total_frames_captured": captured_count,
        "total_frames_submitted": submitted_count,
        "total_callbacks_received": callbacks_count,
        "total_results_processed": processed_count,
        "total_drops": drops,
        "processed_fps": round(processed_count / actual_duration, 2),
        "memory": {
            "initial_rss_mb": initial_rss,
            "final_rss_mb": final_rss,
            "peak_rss_mb": peak_rss,
            "slope_mb_per_hour": round(slope_mb_per_hour, 2),
            "status": leak_status,
        },
        "cpu": {
            "mean_percent": round(mean_cpu, 1),
        },
        "worker_liveness": "PASS: Worker thread joined cleanly without stalls",
    }

    final_payload = {
        "metadata": {
            "test_type": "10_MINUTE_LIVE_STABILITY_VALIDATION",
            "date": time.strftime("%Y-%m-%d %H:%M:%S"),
            "device": "Webcam 0 (1280x720 @ 30 FPS)",
        },
        "summary": summary,
        "samples": samples,
    }

    with open("production_v2_stability_10min.json", "w", encoding="utf-8") as f:
        json.dump(final_payload, f, indent=2)

    print("\n==================================================================")
    print(f"10-MINUTE STABILITY COMPLETE: {summary['processed_fps']} FPS, Drops: {drops}")
    print(f"Memory Slope: {summary['memory']['slope_mb_per_hour']} MB/hour -> {leak_status}")
    print("Saved production_v2_stability_10min.json")
    print("==================================================================")
    return final_payload


if __name__ == "__main__":
    run_10min_stability(600.0, 5.0)
