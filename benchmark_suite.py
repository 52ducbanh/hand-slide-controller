"""Comprehensive Performance Benchmark and Profiling Suite for Hand Slide Controller.

Measures all pipeline stages, scenarios, hardware metrics, memory, CPU, and
generates benchmark_results.json, benchmark_frames.csv, and profile.txt.
"""

from __future__ import annotations
import os
import sys
import time
import json
import csv
import cProfile
import pstats
import subprocess
from collections import deque
from typing import Any

import cv2
import numpy as np
import mediapipe as mp
import psutil
import pyautogui

# Disable pyautogui fail-safe during automated benchmark to avoid corner mouse exception
pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0
# Safe mock for press during benchmark so it doesn't spam keystrokes into active IDE windows
_orig_press = pyautogui.press
pyautogui.press = lambda key: None

from hand_controller.config import AppConfig
from hand_controller.models import HandDetection, SlideAction
from hand_controller.gestures import GestureRecognizer
from hand_controller.tracker import HandTracker
from hand_controller.state_machine import GestureStateMachine
from hand_controller.actions import ActionDispatcher
from hand_controller.renderer import Renderer

_BaseOptions = mp.tasks.BaseOptions
_HandLandmarker = mp.tasks.vision.HandLandmarker
_HandLandmarkerOptions = mp.tasks.vision.HandLandmarkerOptions
_RunningMode = mp.tasks.vision.RunningMode

WIN_NAME = "Benchmark Gesture Controller"


def get_gpu_info() -> dict[str, Any]:
    gpu_data = {
        "utilization_gpu": None,
        "utilization_memory": None,
        "vram_total_mb": None,
        "vram_used_mb": None,
        "vram_free_mb": None,
        "temperature_c": None,
    }
    try:
        res = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,utilization.memory,memory.total,memory.used,memory.free,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if res.returncode == 0 and res.stdout.strip():
            parts = [p.strip() for p in res.stdout.strip().split(",")]
            gpu_data["utilization_gpu"] = float(parts[0])
            gpu_data["utilization_memory"] = float(parts[1])
            gpu_data["vram_total_mb"] = float(parts[2])
            gpu_data["vram_used_mb"] = float(parts[3])
            gpu_data["vram_free_mb"] = float(parts[4])
            gpu_data["temperature_c"] = float(parts[5])
    except Exception:
        pass
    return gpu_data


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


def make_padded_1280x720(src_bgr: np.ndarray) -> np.ndarray:
    canvas = np.zeros((720, 1280, 3), dtype=np.uint8)
    h, w = src_bgr.shape[:2]
    scale = min(720 / h, 1280 / w)
    nw, nh = int(w * scale), int(h * scale)
    resized = cv2.resize(src_bgr, (nw, nh))
    y = (720 - nh) // 2
    x = (1280 - nw) // 2
    canvas[y : y + nh, x : x + nw] = resized
    return canvas


class BenchmarkRunner:

    def __init__(self, cfg: AppConfig | None = None) -> None:
        self.cfg = cfg or AppConfig()
        self.proc = psutil.Process(os.getpid())

    def measure_startup(self) -> dict[str, float]:
        t0 = time.perf_counter()
        cap = cv2.VideoCapture(self.cfg.camera.index, cv2.CAP_MSMF)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.cfg.camera.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.cfg.camera.height)
        cap.set(cv2.CAP_PROP_FPS, self.cfg.camera.fps)
        t_cam_ready = time.perf_counter()

        options = _HandLandmarkerOptions(
            base_options=_BaseOptions(model_asset_path=self.cfg.model_path),
            running_mode=_RunningMode.VIDEO,
            num_hands=self.cfg.num_hands,
            min_hand_detection_confidence=self.cfg.min_hand_detection_confidence,
            min_hand_presence_confidence=self.cfg.min_hand_presence_confidence,
            min_tracking_confidence=self.cfg.min_tracking_confidence,
        )

        with _HandLandmarker.create_from_options(options) as lm:
            t_model_ready = time.perf_counter()
            ret, frame = cap.read()
            if ret:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                _ = lm.detect_for_video(mp_img, 1)
            t_first_frame = time.perf_counter()

        cap.release()

        return {
            "process_start_to_cam_ready_ms": (t_cam_ready - t0) * 1000,
            "cam_ready_to_model_ready_ms": (t_model_ready - t_cam_ready) * 1000,
            "model_ready_to_first_frame_ms": (
                t_first_frame - t_model_ready
            )
            * 1000,
            "total_startup_ms": (t_first_frame - t0) * 1000,
        }

    def measure_camera_detailed(
        self, num_frames: int = 300
    ) -> dict[str, Any]:
        print(f"--- Measuring Camera I/O ({num_frames} frames) ---")
        cap = cv2.VideoCapture(self.cfg.camera.index, cv2.CAP_MSMF)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.cfg.camera.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.cfg.camera.height)
        cap.set(cv2.CAP_PROP_FPS, self.cfg.camera.fps)

        read_times_ms: list[float] = []
        frame_intervals_ms: list[float] = []

        last_time = time.perf_counter()
        for i in range(num_frames):
            t0 = time.perf_counter()
            ret, _ = cap.read()
            t1 = time.perf_counter()
            if not ret:
                break
            read_times_ms.append((t1 - t0) * 1000.0)
            if i > 0:
                frame_intervals_ms.append((t0 - last_time) * 1000.0)
            last_time = t0

        cap.release()

        read_stats = compute_stats(read_times_ms)
        interval_stats = compute_stats(frame_intervals_ms)
        effective_fps = (
            (1000.0 / interval_stats["mean"])
            if interval_stats["mean"] > 0
            else 0.0
        )

        return {
            "read_stats": read_stats,
            "interval_stats": interval_stats,
            "effective_fps": effective_fps,
        }

    def run_scenario(
        self,
        scenario_name: str,
        duration_seconds: float,
        frame_source: str = "camera",
        static_image_bgr: np.ndarray | None = None,
        warmup_frames: int = 60,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        print(
            f"--- Starting {scenario_name} (duration: {duration_seconds}s,"
            f" warmup: {warmup_frames}f, source: {frame_source}) ---"
        )

        recognizer = GestureRecognizer(self.cfg.gesture)
        tracker = HandTracker(self.cfg.tracking)
        state_machine = GestureStateMachine(self.cfg.tracking)
        dispatcher = ActionDispatcher(
            cooldown=self.cfg.global_action_cooldown,
            audio_feedback=self.cfg.audio_feedback,
        )
        renderer = Renderer(debug=self.cfg.debug)

        cap = None
        if frame_source == "camera":
            cap = cv2.VideoCapture(self.cfg.camera.index, cv2.CAP_MSMF)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.cfg.camera.width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.cfg.camera.height)
            cap.set(cv2.CAP_PROP_FPS, self.cfg.camera.fps)

        options = _HandLandmarkerOptions(
            base_options=_BaseOptions(model_asset_path=self.cfg.model_path),
            running_mode=_RunningMode.VIDEO,
            num_hands=self.cfg.num_hands,
            min_hand_detection_confidence=self.cfg.min_hand_detection_confidence,
            min_hand_presence_confidence=self.cfg.min_hand_presence_confidence,
            min_tracking_confidence=self.cfg.min_tracking_confidence,
        )

        cv2.namedWindow(WIN_NAME, cv2.WINDOW_AUTOSIZE)

        records: list[dict[str, Any]] = []
        cpu_percentages: list[float] = []
        sys_cpu_percentages: list[float] = []
        ram_rss_mb: list[float] = []
        gpu_usages: list[float] = []
        vram_usages: list[float] = []

        with _HandLandmarker.create_from_options(options) as landmarker:
            start_time = time.time()
            last_timestamp_ms = -1
            fps_history: deque[float] = deque(maxlen=20)
            rgb_buf: np.ndarray | None = None

            total_frames = 0
            benchmark_started = False
            bench_start_time = 0.0

            while True:
                t_frame_start = time.perf_counter_ns()

                # 1. Camera Read / Frame Acquisition
                t0 = time.perf_counter_ns()
                if frame_source == "camera":
                    success, frame = cap.read()
                    if not success:
                        break
                else:
                    frame = static_image_bgr.copy()
                    # Simulate video capture interval pacing
                    time.sleep(0.002)
                t_cam = time.perf_counter_ns() - t0

                # 2. Frame Flip
                t0 = time.perf_counter_ns()
                cv2.flip(frame, 1, dst=frame)
                t_flip = time.perf_counter_ns() - t0

                now = time.time()
                fps_history.append(now)
                dt = fps_history[-1] - fps_history[0]
                fps = (
                    (len(fps_history) - 1) / dt
                    if len(fps_history) > 1 and dt > 0
                    else 30.0
                )

                # 3. Color Conversion
                t0 = time.perf_counter_ns()
                if rgb_buf is None or rgb_buf.shape != frame.shape:
                    rgb_buf = np.empty_like(frame)
                cv2.cvtColor(frame, cv2.COLOR_BGR2RGB, dst=rgb_buf)
                t_color = time.perf_counter_ns() - t0

                # 4. mp.Image Creation
                t0 = time.perf_counter_ns()
                mp_image = mp.Image(
                    image_format=mp.ImageFormat.SRGB, data=rgb_buf
                )
                t_mp_img = time.perf_counter_ns() - t0

                timestamp_ms = int((now - start_time) * 1000)
                if timestamp_ms <= last_timestamp_ms:
                    timestamp_ms = last_timestamp_ms + 1
                last_timestamp_ms = timestamp_ms

                # 5. MediaPipe Inference
                t0 = time.perf_counter_ns()
                result = landmarker.detect_for_video(mp_image, timestamp_ms)
                t_inference = time.perf_counter_ns() - t0

                # 6. Hand Extraction, Detections & Gesture Classification
                t0 = time.perf_counter_ns()
                detections: list[HandDetection] = []
                t_gest_sum = 0
                if result.hand_landmarks:
                    for i, landmarks in enumerate(result.hand_landmarks):
                        raw_label = "Unknown"
                        raw_score = 0.0
                        if (
                            result.handedness
                            and i < len(result.handedness)
                            and result.handedness[i]
                        ):
                            cat = result.handedness[i][0]
                            raw_label = cat.category_name
                            raw_score = cat.score

                        tg0 = time.perf_counter_ns()
                        gesture_result = recognizer.classify(landmarks)
                        t_gest_sum += time.perf_counter_ns() - tg0

                        wrist = landmarks[0]
                        detections.append(
                            HandDetection(
                                landmarks=landmarks,
                                wrist_x=wrist.x,
                                wrist_y=wrist.y,
                                gesture_result=gesture_result,
                                raw_label=raw_label,
                                raw_score=raw_score,
                            )
                        )
                t_res_proc = (time.perf_counter_ns() - t0) - t_gest_sum

                # 7. Tracking
                t0 = time.perf_counter_ns()
                assignments = tracker.assign(detections, now)
                detection_track_map: dict[int, int] = {}
                t_tracker = time.perf_counter_ns() - t0

                # 8. State Machine & Action Dispatch
                t_sm_sum = 0
                t_act_sum = 0
                for det_idx, track_id in assignments.items():
                    detection = detections[det_idx]
                    track = tracker.tracks[track_id]

                    tt0 = time.perf_counter_ns()
                    tracker.update_position(track, detection, now)
                    t_tracker += time.perf_counter_ns() - tt0
                    detection_track_map[det_idx] = track_id

                    ts0 = time.perf_counter_ns()
                    action = state_machine.update(track, detection, now)
                    t_sm_sum += time.perf_counter_ns() - ts0

                    if action != SlideAction.NONE:
                        ta0 = time.perf_counter_ns()
                        dispatched = dispatcher.dispatch(action, now)
                        if dispatched:
                            state_machine.latch(track)
                        t_act_sum += time.perf_counter_ns() - ta0

                tt1 = time.perf_counter_ns()
                tracker.expire_lost_tracks(assignments.values(), now)
                t_tracker += time.perf_counter_ns() - tt1

                # 9. Rendering
                t0 = time.perf_counter_ns()
                renderer.draw_frame(
                    frame,
                    tracker.tracks,
                    detection_track_map,
                    detections,
                    dispatcher,
                    now,
                    fps=fps,
                )
                t_render = time.perf_counter_ns() - t0

                # 10. GUI Display (imshow / waitKey)
                t0 = time.perf_counter_ns()
                cv2.imshow(WIN_NAME, frame)
                _ = cv2.waitKey(1) & 0xFF
                t_gui = time.perf_counter_ns() - t0

                t_frame_total = time.perf_counter_ns() - t_frame_start
                total_frames += 1

                if not benchmark_started:
                    if total_frames >= warmup_frames:
                        benchmark_started = True
                        bench_start_time = time.time()
                        print(
                            f"Warmup complete ({warmup_frames} frames). Collecting"
                            " metrics..."
                        )
                    continue

                num_detected_hands = (
                    len(result.hand_landmarks) if result.hand_landmarks else 0
                )
                rec = {
                    "scenario": scenario_name,
                    "frame_idx": len(records),
                    "camera_read_ms": t_cam / 1e6,
                    "frame_flip_ms": t_flip / 1e6,
                    "color_convert_ms": t_color / 1e6,
                    "mp_image_ms": t_mp_img / 1e6,
                    "mediapipe_ms": t_inference / 1e6,
                    "result_processing_ms": t_res_proc / 1e6,
                    "gesture_ms": t_gest_sum / 1e6,
                    "tracker_ms": t_tracker / 1e6,
                    "state_machine_ms": t_sm_sum / 1e6,
                    "action_dispatch_ms": t_act_sum / 1e6,
                    "render_ms": t_render / 1e6,
                    "gui_ms": t_gui / 1e6,
                    "total_frame_ms": t_frame_total / 1e6,
                    "fps": fps,
                    "hands_count": num_detected_hands,
                }
                records.append(rec)

                if len(records) % 30 == 0:
                    cpu_percentages.append(self.proc.cpu_percent())
                    sys_cpu_percentages.append(psutil.cpu_percent())
                    ram_rss_mb.append(
                        self.proc.memory_info().rss / (1024 * 1024)
                    )
                    g_info = get_gpu_info()
                    if g_info["utilization_gpu"] is not None:
                        gpu_usages.append(g_info["utilization_gpu"])
                        vram_usages.append(g_info["vram_used_mb"])

                if time.time() - bench_start_time >= duration_seconds:
                    break

        if cap is not None:
            cap.release()
        cv2.destroyAllWindows()

        stage_names = [
            ("camera_read", "camera_read_ms"),
            ("frame_flip", "frame_flip_ms"),
            ("color_convert", "color_convert_ms"),
            ("mp_image", "mp_image_ms"),
            ("mediapipe", "mediapipe_ms"),
            ("result_proc", "result_processing_ms"),
            ("gesture", "gesture_ms"),
            ("tracker", "tracker_ms"),
            ("state_machine", "state_machine_ms"),
            ("action_dispatch", "action_dispatch_ms"),
            ("render", "render_ms"),
            ("gui", "gui_ms"),
            ("total_frame", "total_frame_ms"),
        ]

        summary_stages = {}
        total_mean = (
            np.mean([r["total_frame_ms"] for r in records]) if records else 1.0
        )

        for name, key in stage_names:
            vals = [r[key] for r in records]
            st = compute_stats(vals)
            st["percent_total"] = (
                (st["mean"] / total_mean * 100.0) if total_mean > 0 else 0.0
            )
            summary_stages[name] = st

        fps_vals = [r["fps"] for r in records]
        fps_stats = compute_stats(fps_vals)
        total_wall_time = time.time() - bench_start_time
        true_throughput_fps = len(records) / total_wall_time if total_wall_time > 0 else 0.0

        summary = {
            "scenario": scenario_name,
            "duration_seconds": duration_seconds,
            "actual_wall_time_s": total_wall_time,
            "frame_count": len(records),
            "stages": summary_stages,
            "fps": {
                "loop_throughput_fps": true_throughput_fps,
                "instantaneous_mean": fps_stats["mean"],
                "median": fps_stats["median"],
                "min": fps_stats["min"],
                "p95_frame_time_ms": summary_stages["total_frame"]["p95"],
                "p99_frame_time_ms": summary_stages["total_frame"]["p99"],
            },
            "system": {
                "proc_cpu_mean": float(np.mean(cpu_percentages))
                if cpu_percentages
                else 0.0,
                "proc_cpu_p95": float(np.percentile(cpu_percentages, 95))
                if cpu_percentages
                else 0.0,
                "sys_cpu_mean": float(np.mean(sys_cpu_percentages))
                if sys_cpu_percentages
                else 0.0,
                "ram_rss_mean_mb": float(np.mean(ram_rss_mb))
                if ram_rss_mb
                else 0.0,
                "ram_rss_peak_mb": float(np.max(ram_rss_mb))
                if ram_rss_mb
                else 0.0,
                "gpu_util_mean": float(np.mean(gpu_usages))
                if gpu_usages
                else 0.0,
                "vram_used_mean_mb": float(np.mean(vram_usages))
                if vram_usages
                else 0.0,
            },
        }

        print(
            f"Finished {scenario_name}: {len(records)} frames, avg"
            f" {fps_stats['mean']:.1f} FPS, total mean"
            f" {summary_stages['total_frame']['mean']:.2f} ms"
        )
        return summary, records


def run_full_suite():
    print("================================================================")
    print("      HAND SLIDE CONTROLLER - COMPREHENSIVE BENCHMARK SUITE      ")
    print("================================================================")

    cfg = AppConfig()
    runner = BenchmarkRunner(cfg)

    # 1. Startup measurement
    print("\n[Step 1/7] Measuring Startup Performance...")
    startup_metrics = runner.measure_startup()
    print("Startup metrics:", json.dumps(startup_metrics, indent=2))

    # 2. Camera detailed measurement
    print("\n[Step 2/7] Measuring Camera I/O detailed performance...")
    camera_metrics = runner.measure_camera_detailed(num_frames=200)
    print("Camera I/O metrics:", json.dumps(camera_metrics, indent=2))

    # Load test images for hand scenarios
    img_sample = cv2.imread("woman_hands.jpg")
    canvas_2h = make_padded_1280x720(img_sample)
    h, w = img_sample.shape[:2]
    canvas_1h = make_padded_1280x720(img_sample[:, : int(w * 0.55)])

    all_records: list[dict[str, Any]] = []
    scenarios_summary = {}

    # 3. Scenario A: No Hand (Live Camera, 30s)
    print("\n[Step 3/7] Running Scenario A: No Hand (Live Camera, 30s)...")
    s_a, r_a = runner.run_scenario(
        scenario_name="Scenario A (No Hand / Idle Camera)",
        duration_seconds=30.0,
        frame_source="camera",
        warmup_frames=60,
    )
    scenarios_summary["scenario_a_no_hand"] = s_a
    all_records.extend(r_a)

    # 4. Scenario B: 1 Hand Steady (Simulated with padded image, 30s)
    print("\n[Step 4/7] Running Scenario B: 1 Hand Steady (30s)...")
    s_b, r_b = runner.run_scenario(
        scenario_name="Scenario B (1 Hand Steady)",
        duration_seconds=30.0,
        frame_source="static",
        static_image_bgr=canvas_1h,
        warmup_frames=60,
    )
    scenarios_summary["scenario_b_1_hand"] = s_b
    all_records.extend(r_b)

    # 5. Scenario C: Gesture Active (30s)
    print("\n[Step 5/7] Running Scenario C: Gesture Active (30s)...")
    s_c, r_c = runner.run_scenario(
        scenario_name="Scenario C (Gesture Active)",
        duration_seconds=30.0,
        frame_source="static",
        static_image_bgr=canvas_1h,
        warmup_frames=60,
    )
    scenarios_summary["scenario_c_gesture_active"] = s_c
    all_records.extend(r_c)

    # 6. Scenario D: 2 Hands (Padded 2-hand canvas, 30s)
    print("\n[Step 6/7] Running Scenario D: 2 Hands (30s)...")
    s_d, r_d = runner.run_scenario(
        scenario_name="Scenario D (2 Hands)",
        duration_seconds=30.0,
        frame_source="static",
        static_image_bgr=canvas_2h,
        warmup_frames=60,
    )
    scenarios_summary["scenario_d_2_hands"] = s_d
    all_records.extend(r_d)

    # 7. cProfile on the pipeline (300 frames)
    print("\n[Step 7/7] Profiling Python functions with cProfile (300 frames)...")
    profiler = cProfile.Profile()
    profiler.enable()

    options = _HandLandmarkerOptions(
        base_options=_BaseOptions(model_asset_path=cfg.model_path),
        running_mode=_RunningMode.VIDEO,
        num_hands=cfg.num_hands,
        min_hand_detection_confidence=cfg.min_hand_detection_confidence,
        min_hand_presence_confidence=cfg.min_hand_presence_confidence,
        min_tracking_confidence=cfg.min_tracking_confidence,
    )
    recognizer = GestureRecognizer(cfg.gesture)
    tracker = HandTracker(cfg.tracking)
    state_machine = GestureStateMachine(cfg.tracking)
    dispatcher = ActionDispatcher(
        cooldown=cfg.global_action_cooldown, audio_feedback=False
    )
    renderer = Renderer(debug=True)

    with _HandLandmarker.create_from_options(options) as lm:
        rgb_buf = np.empty_like(canvas_1h)
        for i in range(300):
            frame = canvas_1h.copy()
            cv2.flip(frame, 1, dst=frame)
            cv2.cvtColor(frame, cv2.COLOR_BGR2RGB, dst=rgb_buf)
            mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_buf)
            res = lm.detect_for_video(mp_img, i * 33 + 1)
            dets = []
            if res.hand_landmarks:
                for lms in res.hand_landmarks:
                    gr = recognizer.classify(lms)
                    dets.append(
                        HandDetection(
                            landmarks=lms,
                            wrist_x=lms[0].x,
                            wrist_y=lms[0].y,
                            gesture_result=gr,
                        )
                    )
            assigns = tracker.assign(dets, i * 0.033)
            dt_map = {}
            for d_i, t_i in assigns.items():
                t = tracker.tracks[t_i]
                tracker.update_position(t, dets[d_i], i * 0.033)
                dt_map[d_i] = t_i
                a = state_machine.update(t, dets[d_i], i * 0.033)
                if a != SlideAction.NONE:
                    if dispatcher.dispatch(a, i * 0.033):
                        state_machine.latch(t)
            tracker.expire_lost_tracks(assigns.values(), i * 0.033)
            renderer.draw_frame(
                frame,
                tracker.tracks,
                dt_map,
                dets,
                dispatcher,
                i * 0.033,
                fps=30.0,
            )

    profiler.disable()

    # Save cProfile stats to profile.txt and profile_after.txt
    for prof_path in ["profile.txt", "profile_after.txt"]:
        with open(prof_path, "w", encoding="utf-8") as f:
            ps = pstats.Stats(profiler, stream=f).sort_stats("cumulative")
            f.write("=== TOP 30 BY CUMULATIVE TIME ===\n")
            ps.print_stats(30)
            f.write("\n\n=== TOP 30 BY TOTAL (INTERNAL) TIME ===\n")
            ps.sort_stats("time").print_stats(30)
    print("cProfile output written to profile.txt and profile_after.txt")

    # Export benchmark_frames.csv
    if all_records:
        csv_keys = list(all_records[0].keys())
        with open(
            "benchmark_frames.csv", "w", newline="", encoding="utf-8"
        ) as f:
            writer = csv.DictWriter(f, fieldnames=csv_keys)
            writer.writeheader()
            writer.writerows(all_records)
        print(
            "Frame-by-frame data saved to benchmark_frames.csv"
            f" ({len(all_records)} frames)"
        )

    # Export benchmark_results.json and benchmark_after.json
    final_output = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "startup": startup_metrics,
        "camera": camera_metrics,
        "scenarios": scenarios_summary,
    }
    for res_path in ["benchmark_results.json", "benchmark_after.json"]:
        with open(res_path, "w", encoding="utf-8") as f:
            json.dump(final_output, f, indent=2)
    print("Complete benchmark summary written to benchmark_results.json and benchmark_after.json")

    print("\nBenchmark run completed successfully!")


if __name__ == "__main__":
    run_full_suite()
