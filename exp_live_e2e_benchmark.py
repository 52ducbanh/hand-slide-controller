"""Phase 4: Full Live End-to-End Pipeline Benchmark on Physical Camera.

Measures full live pipeline on webcam device 0:
- Physical Camera Capture (cv2.CAP_MSMF, 1280x720 @ 30 FPS)
- In-place flip & Reusable RGB buffer
- MediaPipe detect_async()
- Dedicated event-driven Control Worker callback
- Gesture classification & HandTracker
- GestureStateMachine & ActionDispatcher

Latency stages recorded:
- Capture -> Submit
- Submit -> Callback
- Callback -> Gesture
- Gesture -> ActionStart
- Action dispatch duration
- Capture -> Action

Outputs:
- live_e2e_latency.json
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


def compute_stats(vals: list[float]) -> dict[str, float]:
    if not vals:
        return {"samples": 0, "mean": 0.0, "median": 0.0, "p50": 0.0, "p90": 0.0, "p95": 0.0, "p99": 0.0, "min": 0.0, "max": 0.0, "std": 0.0}
    arr = np.array(vals, dtype=np.float64)
    return {
        "samples": len(vals),
        "mean": round(float(np.mean(arr)), 3),
        "median": round(float(np.median(arr)), 3),
        "p50": round(float(np.percentile(arr, 50)), 3),
        "p90": round(float(np.percentile(arr, 90)), 3),
        "p95": round(float(np.percentile(arr, 95)), 3),
        "p99": round(float(np.percentile(arr, 99)), 3),
        "min": round(float(np.min(arr)), 3),
        "max": round(float(np.max(arr)), 3),
        "std": round(float(np.std(arr)), 3),
    }


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


def run_live_e2e_benchmark(num_frames: int = 300) -> dict[str, Any]:
    print("==================================================================")
    print("EXP 4: FULL LIVE END-TO-END PIPELINE BENCHMARK (PHYSICAL WEBCAM)")
    print("==================================================================")

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

    c2s_ms: list[float] = []
    s2cb_ms: list[float] = []
    cb2g_ms: list[float] = []
    c2g_ms: list[float] = []
    c2a_ms: list[float] = []
    g_done2a_ms: list[float] = []
    action_dur_ms: list[float] = []
    real_actions: list[dict[str, Any]] = []

    callbacks_count = 0
    submitted_count = 0
    captured_count = 0
    processed_count = 0
    drops = 0

    def on_result(result, image, ts_ms: int) -> None:
        nonlocal latest_worker_packet, callbacks_count, drops
        cb_time = time.perf_counter()
        callbacks_count += 1
        with shared_lock:
            if ts_ms in pending_records:
                rec = pending_records.pop(ts_ms)
                s2cb = (cb_time - rec.submit_time) * 1000.0
                s2cb_ms.append(s2cb)
                latest_worker_packet = _WorkerPacket(result, ts_ms, rec.frame_idx, rec.capture_time, rec.submit_time, cb_time)
                new_result_event.set()
            # Prune older pending
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
            t_g_start = time.perf_counter()
            cb2g = (t_g_start - packet.callback_time) * 1000.0
            cb2g_ms.append(cb2g)

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
                    t_g_done = time.perf_counter()
                    c2g_ms.append((t_g_done - packet.capture_time) * 1000.0)

                    t_act_start = time.perf_counter()
                    g_done2a_ms.append((t_act_start - t_g_done) * 1000.0)

                    dispatched = dispatcher.dispatch(act, now_sec)
                    t_act_end = time.perf_counter()
                    act_dur = (t_act_end - t_act_start) * 1000.0
                    action_dur_ms.append(act_dur)

                    if dispatched:
                        state_machine.latch(track)
                        c2a = (t_act_end - packet.capture_time) * 1000.0
                        c2a_ms.append(c2a)
                        real_actions.append({"frame": packet.frame_idx, "action": act.name, "latency_ms": round(c2a, 2)})

            tracker.expire_lost_tracks(assignments.values(), now_sec)

    worker_thread = threading.Thread(target=control_worker, daemon=False, name="LiveControlWorker")
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
    actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    actual_fps = float(cap.get(cv2.CAP_PROP_FPS))
    print(f"Physical camera opened: {actual_w}x{actual_h} @ {actual_fps:.1f} FPS")

    # Warmup 30 frames
    print("Warming up 30 frames...")
    for _ in range(30):
        cap.read()

    print(f"Streaming {num_frames} frames through live physical pipeline...")
    frame_idx = 0
    last_timestamp_ms = -1
    t_stream_start = time.perf_counter()

    while frame_idx < num_frames:
        t_cap_start = time.perf_counter()
        ret, frame = cap.read()
        t_cap_end = time.perf_counter()

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
        c2s = (t_sub - t_cap_end) * 1000.0
        c2s_ms.append(c2s)

        with shared_lock:
            pending_records[timestamp_ms] = _FrameRecord(frame_idx, t_cap_end, t_sub, timestamp_ms)
        submitted_count += 1
        landmarker.detect_async(mp_image, timestamp_ms)

    # Wait for remaining callbacks
    time.sleep(0.5)
    t_stream_end = time.perf_counter()

    stop_event.set()
    new_result_event.set()
    worker_thread.join(timeout=2.0)
    landmarker.close()
    cap.release()

    duration_s = t_stream_end - t_stream_start
    processed_fps = processed_count / duration_s if duration_s > 0 else 0.0

    print(f"\nLive Streaming Complete:")
    print(f"  Frames Captured: {captured_count}, Submitted: {submitted_count}, Processed: {processed_count}, Drops: {drops}")
    print(f"  Processed FPS: {processed_fps:.2f}")
    print(f"  Capture->Submit: Mean = {np.mean(c2s_ms):.2f} ms")
    print(f"  Submit->Callback: Mean = {np.mean(s2cb_ms):.2f} ms, Median = {np.median(s2cb_ms):.2f} ms, P95 = {np.percentile(s2cb_ms, 95):.2f} ms")
    print(f"  Callback->Gesture: Mean = {np.mean(cb2g_ms):.2f} ms")
    if c2a_ms:
        print(f"  Capture->Action: Mean = {np.mean(c2a_ms):.2f} ms, Median = {np.median(c2a_ms):.2f} ms, P95 = {np.percentile(c2a_ms, 95):.2f} ms")
    else:
        print("  Capture->Action: (No actions triggered during test window; gestures were idle/none)")

    final_payload = {
        "metadata": {
            "test_type": "LIVE_PHYSICAL_PIPELINE_END_TO_END",
            "date": time.strftime("%Y-%m-%d %H:%M:%S"),
            "backend": "cv2.CAP_MSMF (Constructor Params)",
            "device_index": 0,
            "resolution": f"{actual_w}x{actual_h}",
            "fps": actual_fps,
            "clock_domain": "time.perf_counter() monotonic wall clock",
        },
        "counts": {
            "captured_frames": captured_count,
            "submitted_frames": submitted_count,
            "callbacks_received": callbacks_count,
            "processed_results": processed_count,
            "mediapipe_input_drops": drops,
        },
        "throughput": {
            "duration_sec": round(duration_s, 2),
            "processed_fps": round(processed_fps, 2),
        },
        "latencies": {
            "capture_to_submit_ms": compute_stats(c2s_ms),
            "submit_to_callback_ms": compute_stats(s2cb_ms),
            "callback_to_gesture_ms": compute_stats(cb2g_ms),
            "capture_to_gesture_ms": compute_stats(c2g_ms),
            "gesture_done_to_action_start_ms": compute_stats(g_done2a_ms),
            "action_dispatch_duration_ms": compute_stats(action_dur_ms),
            "capture_to_action_ms": compute_stats(c2a_ms),
        },
        "actions_dispatched": real_actions,
    }

    with open("live_e2e_latency.json", "w", encoding="utf-8") as f:
        json.dump(final_payload, f, indent=2)

    print("\nSaved live_e2e_latency.json")
    print("==================================================================")
    return final_payload


if __name__ == "__main__":
    run_live_e2e_benchmark(300)
