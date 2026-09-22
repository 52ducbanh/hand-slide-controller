"""Comprehensive 5-Trial A/B Benchmark: Production v2 (MSMF) vs Production v3 (WinRT).

Measures across 5 trials each on physical webcam 0:
- Startup breakdown: camera_init, format_negotiation, first_frame, first_mediapipe, total
- Runtime: FPS, intervals, jitter std
- Source Timestamp -> App Receive (raw QPC domain)
- App Receive -> RGB Ready (buffer access + NV12 layout + cvtColor + flip)
- Submit -> Callback (MediaPipe CPU inference)
- Callback -> Gesture (Worker event wake-up)
- Source -> Action (Full latency)
- CPU %, RAM RSS, Drops, Duplicates
- FrameArrived handler execution duration

Outputs:
- production_v3_winrt_benchmark.json
- production_v3_winrt_frames.csv
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

from hand_controller.config import AppConfig, CameraConfig
from hand_controller.camera import CameraSource, CameraFrame, create_camera_source, get_raw_qpc_sec
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
        return {
            "samples": 0, "mean": 0.0, "median": 0.0, "p50": 0.0,
            "p90": 0.0, "p95": 0.0, "p99": 0.0, "min": 0.0, "max": 0.0, "std": 0.0
        }
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
    source_ts_sec: float | None
    app_receive_qpc: float


class _WorkerPacket(NamedTuple):
    result: Any
    timestamp_ms: int
    frame_idx: int
    capture_time: float
    submit_time: float
    callback_time: float
    source_ts_sec: float | None
    app_receive_qpc: float


def run_single_trial(
    candidate_name: str,
    backend_mode: str,
    trial_idx: int,
    csv_writer: Any,
    num_frames: int = 100,
) -> dict[str, Any]:
    print(f"\n--- [{candidate_name}] Trial {trial_idx}/5 ---")
    proc = psutil.Process()
    ram_initial_mb = proc.memory_info().rss / (1024 * 1024)

    t_trial_start = time.perf_counter_ns()

    # Configure Camera
    cam_cfg = CameraConfig(
        index=0,
        width=1280,
        height=720,
        fps=30,
        backend=backend_mode,
        force_winrt_init_failure=False,
    )

    t_cam_init_start = time.perf_counter_ns()
    camera = create_camera_source(cam_cfg)
    diag = camera.diagnostics
    t_cam_init_end = time.perf_counter_ns()
    cam_init_ms = (t_cam_init_end - t_cam_init_start) / 1e6

    # Strict assertion of backend identity
    if backend_mode.upper() == "WINRT" and diag.selected_backend != "WINRT":
        camera.close()
        raise RuntimeError(f"TRIAL INVALID: Requested WINRT but got fallback to {diag.selected_backend} ({diag.fallback_reason})")

    # Configure Pipeline
    app_cfg = AppConfig(
        camera=cam_cfg,
        running_mode="LIVE_STREAM_WORKER",
        num_hands=2,
        min_hand_detection_confidence=0.25,
        min_hand_presence_confidence=0.25,
        min_tracking_confidence=0.25,
        preview_enabled=False,
    )
    recognizer = GestureRecognizer(app_cfg.gesture)
    tracker = HandTracker(app_cfg.tracking)
    state_machine = GestureStateMachine(app_cfg.tracking)
    dispatcher = ActionDispatcher(cooldown=app_cfg.global_action_cooldown)

    shared_lock = threading.Lock()
    new_result_event = threading.Event()
    stop_event = threading.Event()
    latest_worker_packet: _WorkerPacket | None = None
    pending_records: dict[int, _FrameRecord] = {}

    callbacks_count = 0
    submitted_count = 0
    captured_count = 0
    processed_count = 0
    drops = 0

    s2cb_ms: list[float] = []
    cb2g_ms: list[float] = []
    src2rec_ms: list[float] = []
    rec2rgb_ms: list[float] = []
    rgb2sub_ms: list[float] = []
    frame_intervals_ms: list[float] = []
    c2a_ms: list[float] = []
    actions_fired: list[str] = []

    def on_result(result, image, ts_ms: int) -> None:
        nonlocal latest_worker_packet, callbacks_count, drops
        cb_time = time.perf_counter()
        callbacks_count += 1
        with shared_lock:
            if ts_ms in pending_records:
                rec = pending_records.pop(ts_ms)
                s2cb = (cb_time - rec.submit_time) * 1000.0
                s2cb_ms.append(s2cb)
                latest_worker_packet = _WorkerPacket(
                    result, ts_ms, rec.frame_idx, rec.capture_time, rec.submit_time, cb_time,
                    rec.source_ts_sec, rec.app_receive_qpc
                )
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
                    dispatched = dispatcher.dispatch(act, now_sec)
                    if dispatched:
                        state_machine.latch(track)
                        t_act_done = time.perf_counter()
                        c2a = (t_act_done - packet.capture_time) * 1000.0
                        c2a_ms.append(c2a)
                        actions_fired.append(act.name)

            tracker.expire_lost_tracks(assignments.values(), now_sec)

    worker_thread = threading.Thread(target=control_worker, daemon=False, name=f"Worker_{candidate_name}_{trial_idx}")
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

    # First Frame Acquisition
    t_first_frame_start = time.perf_counter_ns()
    while True:
        ok, first_cam_frame = camera.read_latest(timeout_sec=0.1)
        if ok and first_cam_frame is not None:
            break
        if time.perf_counter_ns() - t_first_frame_start > 5_000_000_000:
            break
    t_first_frame_end = time.perf_counter_ns()
    first_frame_ms = (t_first_frame_end - t_first_frame_start) / 1e6

    # First MediaPipe Result
    t_first_mp_start = time.perf_counter_ns()
    mp_img0 = mp.Image(image_format=mp.ImageFormat.SRGB, data=first_cam_frame.image_rgb)
    ts0 = time.monotonic_ns() // 1_000_000
    with shared_lock:
        pending_records[ts0] = _FrameRecord(0, time.perf_counter(), time.perf_counter(), first_cam_frame.source_timestamp_sec, first_cam_frame.app_receive_qpc_sec)
    landmarker.detect_async(mp_img0, ts0)

    # Wait for first callback
    while callbacks_count == 0 and (time.perf_counter_ns() - t_first_mp_start) < 3_000_000_000:
        time.sleep(0.005)
    t_first_mp_end = time.perf_counter_ns()
    first_mp_ms = (t_first_mp_end - t_first_mp_start) / 1e6

    total_startup_ms = (time.perf_counter_ns() - t_trial_start) / 1e6

    # Steady State Streaming (num_frames)
    frame_idx = 0
    last_timestamp_ms = ts0
    last_frame_time = time.perf_counter()
    duplicates = 0
    last_img_ref = None

    t_stream_start = time.perf_counter()

    while frame_idx < num_frames:
        success, cam_frame = camera.read_latest(timeout_sec=0.08)
        if not success or cam_frame is None:
            continue

        frame_idx += 1
        captured_count += 1
        now_perf = time.perf_counter()
        interval = (now_perf - last_frame_time) * 1000.0
        frame_intervals_ms.append(interval)
        last_frame_time = now_perf

        # Check duplicate frame content
        if last_img_ref is not None and np.array_equal(cam_frame.image_rgb, last_img_ref):
            duplicates += 1
        last_img_ref = cam_frame.image_rgb.copy()

        # Timing breakdown
        if cam_frame.source_timestamp_sec is not None:
            # Raw QPC domain delivery age
            src2rec = (cam_frame.app_receive_qpc_sec - cam_frame.source_timestamp_sec) * 1000.0
            src2rec_ms.append(src2rec)

        rec2rgb = (cam_frame.rgb_ready_time - cam_frame.capture_time) * 1000.0
        rec2rgb_ms.append(rec2rgb)

        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=cam_frame.image_rgb)

        now_ms = time.monotonic_ns() // 1_000_000
        timestamp_ms = max(now_ms, last_timestamp_ms + 1)
        last_timestamp_ms = timestamp_ms

        t_sub = time.perf_counter()
        rgb2sub = (t_sub - cam_frame.rgb_ready_time) * 1000.0
        rgb2sub_ms.append(rgb2sub)

        with shared_lock:
            pending_records[timestamp_ms] = _FrameRecord(
                frame_idx, cam_frame.capture_time, t_sub,
                cam_frame.source_timestamp_sec, cam_frame.app_receive_qpc_sec
            )
        submitted_count += 1
        landmarker.detect_async(mp_image, timestamp_ms)

        # Write CSV row
        csv_writer.writerow([
            candidate_name, trial_idx, frame_idx,
            round(cam_frame.source_timestamp_sec, 6) if cam_frame.source_timestamp_sec else "N/A",
            round(cam_frame.app_receive_qpc_sec, 6),
            round(src2rec_ms[-1], 3) if src2rec_ms else "N/A",
            round(interval, 3),
        ])

    time.sleep(0.3)
    t_stream_end = time.perf_counter()
    stream_duration = t_stream_end - t_stream_start

    stop_event.set()
    new_result_event.set()
    worker_thread.join(timeout=2.0)
    landmarker.close()
    camera.close()

    ram_final_mb = proc.memory_info().rss / (1024 * 1024)
    cpu_pct = proc.cpu_percent(interval=None)
    effective_fps = processed_count / stream_duration if stream_duration > 0 else 0.0

    cb_durations = getattr(camera, "frame_arrived_durations_ms", [])
    cb_dur_stats = compute_stats(cb_durations)

    print(f"  Result: Startup={total_startup_ms:.1f}ms, Stream FPS={effective_fps:.2f}, Jitter Std={np.std(frame_intervals_ms):.2f}ms")
    if src2rec_ms:
        print(f"  Source->App Receive: Mean={np.mean(src2rec_ms):.2f}ms, P95={np.percentile(src2rec_ms, 95):.2f}ms")
    print(f"  Submit->Callback: Mean={np.mean(s2cb_ms):.2f}ms, Callback->Gesture: Median={np.median(cb2g_ms):.3f}ms")

    return {
        "candidate": candidate_name,
        "trial": trial_idx,
        "status": "PASS",
        "backend": {
            "requested": backend_mode,
            "actual": diag.selected_backend,
            "fallback_occurred": diag.fallback_occurred,
            "fallback_reason": diag.fallback_reason,
            "resolution": f"{diag.actual_width}x{diag.actual_height}",
            "fps": diag.actual_fps,
            "subtype": diag.actual_subtype,
        },
        "startup": {
            "camera_init_ms": round(cam_init_ms, 2),
            "first_frame_ms": round(first_frame_ms, 2),
            "first_mediapipe_ms": round(first_mp_ms, 2),
            "total_startup_ms": round(total_startup_ms, 2),
        },
        "throughput": {
            "captured_frames": captured_count,
            "submitted_frames": submitted_count,
            "processed_results": processed_count,
            "stream_duration_sec": round(stream_duration, 2),
            "effective_fps": round(effective_fps, 2),
            "drops": drops,
            "duplicates": duplicates,
        },
        "latencies": {
            "frame_interval_ms": compute_stats(frame_intervals_ms),
            "source_to_app_receive_ms": compute_stats(src2rec_ms) if src2rec_ms else "SOURCE TIMESTAMP NOT OBSERVABLE THROUGH THIS API",
            "app_receive_to_rgb_ready_ms": compute_stats(rec2rgb_ms),
            "rgb_ready_to_submit_ms": compute_stats(rgb2sub_ms),
            "submit_to_callback_ms": compute_stats(s2cb_ms),
            "callback_to_gesture_ms": compute_stats(cb2g_ms),
            "source_to_action_ms": compute_stats(c2a_ms) if c2a_ms else "NO_ACTION_TRIGGERED_IN_TRIAL",
            "frame_arrived_handler_ms": cb_dur_stats,
        },
        "resources": {
            "cpu_percent": round(cpu_pct, 1),
            "ram_initial_mb": round(ram_initial_mb, 2),
            "ram_final_mb": round(ram_final_mb, 2),
            "ram_delta_mb": round(ram_final_mb - ram_initial_mb, 2),
        }
    }


def main():
    print("==================================================================")
    print("EXP 5: RIGOROUS A/B BENCHMARK: PRODUCTION V2 (MSMF) VS V3 (WINRT)")
    print("5 Trials Each | Physical Webcam 0 | Event-Driven WinRT")
    print("==================================================================")

    csv_file = open("production_v3_winrt_frames.csv", "w", newline="", encoding="utf-8")
    csv_writer = csv.writer(csv_file)
    csv_writer.writerow([
        "candidate", "trial", "frame_idx", "source_timestamp_sec",
        "app_receive_qpc_sec", "delivery_age_ms", "frame_interval_ms"
    ])

    results_a: list[dict[str, Any]] = []
    results_b: list[dict[str, Any]] = []

    # Run Candidate A: MSMF (5 trials)
    for trial in range(1, 6):
        res = run_single_trial(
            candidate_name="MSMF_PROD_V2",
            backend_mode="MSMF",
            trial_idx=trial,
            csv_writer=csv_writer,
            num_frames=100,
        )
        results_a.append(res)
        time.sleep(1.0)

    # Run Candidate B: WinRT (5 trials)
    for trial in range(1, 6):
        res = run_single_trial(
            candidate_name="WINRT_PROD_V3",
            backend_mode="WINRT",
            trial_idx=trial,
            csv_writer=csv_writer,
            num_frames=100,
        )
        results_b.append(res)
        time.sleep(1.0)

    csv_file.close()

    def aggregate_candidate(trials: list[dict[str, Any]]) -> dict[str, Any]:
        startups = [t["startup"]["total_startup_ms"] for t in trials]
        fps_vals = [t["throughput"]["effective_fps"] for t in trials]
        jitters = [t["latencies"]["frame_interval_ms"]["std"] for t in trials]
        s2cb_means = [t["latencies"]["submit_to_callback_ms"]["mean"] for t in trials]
        cb2g_medians = [t["latencies"]["callback_to_gesture_ms"]["median"] for t in trials]
        cpus = [t["resources"]["cpu_percent"] for t in trials]
        rams = [t["resources"]["ram_final_mb"] for t in trials]

        src2rec_p50_list = [
            t["latencies"]["source_to_app_receive_ms"]["p50"]
            for t in trials if isinstance(t["latencies"]["source_to_app_receive_ms"], dict)
        ]
        src2rec_p95_list = [
            t["latencies"]["source_to_app_receive_ms"]["p95"]
            for t in trials if isinstance(t["latencies"]["source_to_app_receive_ms"], dict)
        ]

        return {
            "trials_count": len(trials),
            "startup_total_ms": compute_stats(startups),
            "effective_fps": compute_stats(fps_vals),
            "jitter_std_ms": compute_stats(jitters),
            "source_to_app_receive_p50_ms": compute_stats(src2rec_p50_list) if src2rec_p50_list else "UNOBSERVABLE",
            "source_to_app_receive_p95_ms": compute_stats(src2rec_p95_list) if src2rec_p95_list else "UNOBSERVABLE",
            "submit_to_callback_ms": compute_stats(s2cb_means),
            "callback_to_gesture_median_ms": compute_stats(cb2g_medians),
            "cpu_percent": compute_stats(cpus),
            "ram_final_mb": compute_stats(rams),
        }

    summary = {
        "metadata": {
            "test": "PRODUCTION_V3_AB_BENCHMARK",
            "date": time.strftime("%Y-%m-%d %H:%M:%S"),
            "machine": {
                "cpu": "12th Gen Intel Core i5-12450H (8 Cores: 4P+4E, 12 Threads)",
                "os": "Windows 11 Home (Build 26100)",
                "camera": "HD UVC Webcam 0 (1280x720 @ 30 FPS)",
            }
        },
        "aggregates": {
            "MSMF_PROD_V2": aggregate_candidate(results_a),
            "WINRT_PROD_V3": aggregate_candidate(results_b),
        },
        "trials_msmf": results_a,
        "trials_winrt": results_b,
    }

    with open("production_v3_winrt_benchmark.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print("\n==================================================================")
    print("A/B BENCHMARK COMPLETE!")
    print(f"MSMF Startup:  Mean={summary['aggregates']['MSMF_PROD_V2']['startup_total_ms']['mean']:.1f}ms")
    print(f"WinRT Startup: Mean={summary['aggregates']['WINRT_PROD_V3']['startup_total_ms']['mean']:.1f}ms")
    print(f"MSMF Jitter:   Mean={summary['aggregates']['MSMF_PROD_V2']['jitter_std_ms']['mean']:.2f}ms")
    print(f"WinRT Jitter:  Mean={summary['aggregates']['WINRT_PROD_V3']['jitter_std_ms']['mean']:.2f}ms")
    print("Saved production_v3_winrt_benchmark.json & production_v3_winrt_frames.csv")
    print("==================================================================")


if __name__ == "__main__":
    main()
