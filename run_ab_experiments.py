"""Automated A/B Benchmark Experiment Runner.
Executes systematic A/B tests on the deterministic benchmark_input.mp4:
1. Preview Mode: Preview ON vs Preview OFF -> ab_preview.json
2. Hand Capacity: num_hands=1 vs num_hands=2 -> ab_num_hands.json
3. Running Mode: VIDEO vs LIVE_STREAM -> ab_running_mode.json
4. Inference Resolution: 1280x720 vs 960x540 vs 640x360 -> ab_resolution.json
5. Threaded Capture: Synchronous vs Threaded -> ab_threaded_capture.json
"""

from __future__ import annotations
import os
import sys
import time
import json
import threading
from typing import Any
import cv2
import numpy as np
import mediapipe as mp
import psutil
import pyautogui

# Safety during automated benchmarks
pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0
_orig_press = pyautogui.press
pyautogui.press = lambda k: None

from hand_controller.config import AppConfig, CameraConfig
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

WIN_NAME = "AB Benchmark Test"

def compute_stats(vals: list[float]) -> dict[str, float]:
    if not vals:
        return {"samples": 0, "mean": 0.0, "median": 0.0, "p50": 0.0, "p90": 0.0, "p95": 0.0, "p99": 0.0, "min": 0.0, "max": 0.0, "std": 0.0}
    arr = np.array(vals, dtype=np.float64)
    return {
        "samples": len(vals),
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

# ---------------------------------------------------------
# Core Benchmark Pipeline Execution Engine
# ---------------------------------------------------------
def run_pipeline_on_video(
    video_path: str = "benchmark_input.mp4",
    preview: bool = False,
    num_hands: int = 2,
    infer_width: int = 1280,
    infer_height: int = 720,
    running_mode: Any = _RunningMode.VIDEO,
    threaded_capture: bool = False,
) -> dict[str, Any]:
    proc = psutil.Process(os.getpid())
    cfg = AppConfig(num_hands=num_hands)
    recognizer = GestureRecognizer(cfg.gesture)
    tracker = HandTracker(cfg.tracking)
    state_machine = GestureStateMachine(cfg.tracking)
    dispatcher = ActionDispatcher(cooldown=cfg.global_action_cooldown, audio_feedback=False)
    renderer = Renderer(debug=True)

    if preview:
        cv2.namedWindow(WIN_NAME, cv2.WINDOW_AUTOSIZE)

    # State for LIVE_STREAM mode
    latest_async_result = [None]
    async_lock = threading.Lock()

    def async_callback(result, output_image, timestamp_ms):
        with async_lock:
            latest_async_result[0] = (result, timestamp_ms)

    options = _HandLandmarkerOptions(
        base_options=_BaseOptions(model_asset_path=cfg.model_path),
        running_mode=running_mode,
        num_hands=num_hands,
        min_hand_detection_confidence=cfg.min_hand_detection_confidence,
        min_hand_presence_confidence=cfg.min_hand_presence_confidence,
        min_tracking_confidence=cfg.min_tracking_confidence,
        result_callback=async_callback if running_mode == _RunningMode.LIVE_STREAM else None,
    )

    cap = cv2.VideoCapture(video_path)
    total_video_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    # Measurement arrays
    t_frame_latencies_ms: list[float] = []
    t_inference_ms: list[float] = []
    t_gesture_ms: list[float] = []
    t_tracker_ms: list[float] = []
    t_state_machine_ms: list[float] = []
    t_render_ms: list[float] = []
    t_gui_ms: list[float] = []
    cpu_measurements: list[float] = []
    ram_measurements: list[float] = []

    gestures_detected_by_seg: dict[int, dict[str, int]] = {s: {} for s in range(1, 9)}
    actions_dispatched_by_seg: dict[int, list[str]] = {s: [] for s in range(1, 9)}
    hands_detected_by_seg: dict[int, int] = {s: 0 for s in range(1, 9)}

    rgb_buf = None
    frame_idx = 0
    t_wall_start = time.perf_counter()

    with _HandLandmarker.create_from_options(options) as landmarker:
        while True:
            t_f0 = time.perf_counter_ns()
            ret, frame = cap.read()
            if not ret or frame is None:
                break

            seg_id = (frame_idx // 100) + 1

            # In-place flip
            cv2.flip(frame, 1, dst=frame)

            # Downsample for inference if requested
            if (infer_width, infer_height) != (frame.shape[1], frame.shape[0]):
                infer_frame = cv2.resize(frame, (infer_width, infer_height), interpolation=cv2.INTER_LINEAR)
            else:
                infer_frame = frame

            # Color convert
            if rgb_buf is None or rgb_buf.shape != infer_frame.shape:
                rgb_buf = np.empty_like(infer_frame)
            cv2.cvtColor(infer_frame, cv2.COLOR_BGR2RGB, dst=rgb_buf)
            mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_buf)

            now_sec = frame_idx * (1.0 / 30.0)
            timestamp_ms = int(now_sec * 1000) + 1

            # Inference
            ti0 = time.perf_counter_ns()
            if running_mode == _RunningMode.VIDEO:
                result = landmarker.detect_for_video(mp_img, timestamp_ms)
            else:
                landmarker.detect_async(mp_img, timestamp_ms)
                # In live stream, give small yield if waiting for callback
                time.sleep(0.005)
                with async_lock:
                    res_tuple = latest_async_result[0]
                    result = res_tuple[0] if res_tuple else None
            ti1 = time.perf_counter_ns()
            t_inference_ms.append((ti1 - ti0) / 1e6)

            # Gesture & Tracking
            tg0 = time.perf_counter_ns()
            detections: list[HandDetection] = []
            if result and result.hand_landmarks:
                hands_detected_by_seg[seg_id] += len(result.hand_landmarks)
                for i, lms in enumerate(result.hand_landmarks):
                    raw_lbl = "Unknown"
                    raw_sc = 0.0
                    if result.handedness and i < len(result.handedness) and result.handedness[i]:
                        raw_lbl = result.handedness[i][0].category_name
                        raw_sc = result.handedness[i][0].score

                    gr = recognizer.classify(lms)
                    gname = gr.gesture.name
                    gestures_detected_by_seg[seg_id][gname] = gestures_detected_by_seg[seg_id].get(gname, 0) + 1

                    detections.append(HandDetection(
                        landmarks=lms,
                        wrist_x=lms[0].x,
                        wrist_y=lms[0].y,
                        gesture_result=gr,
                        raw_label=raw_lbl,
                        raw_score=raw_sc,
                    ))
            tg1 = time.perf_counter_ns()
            t_gesture_ms.append((tg1 - tg0) / 1e6)

            # Tracking & State Machine
            tt0 = time.perf_counter_ns()
            assignments = tracker.assign(detections, now_sec)
            dt_map = {}
            for d_idx, t_idx in assignments.items():
                det = detections[d_idx]
                trk = tracker.tracks[t_idx]
                tracker.update_position(trk, det, now_sec)
                dt_map[d_idx] = t_idx
                act = state_machine.update(trk, det, now_sec)
                if act != SlideAction.NONE:
                    if dispatcher.dispatch(act, now_sec):
                        state_machine.latch(trk)
                        actions_dispatched_by_seg[seg_id].append(act.name)
            tracker.expire_lost_tracks(assignments.values(), now_sec)
            tt1 = time.perf_counter_ns()
            t_tracker_ms.append((tt1 - tt0) / 1e6)

            # Rendering
            tr0 = time.perf_counter_ns()
            if preview:
                renderer.draw_frame(
                    frame,
                    tracker.tracks,
                    dt_map,
                    detections,
                    dispatcher,
                    now_sec,
                    fps=30.0,
                )
            tr1 = time.perf_counter_ns()
            t_render_ms.append((tr1 - tr0) / 1e6)

            # GUI Display
            tg0 = time.perf_counter_ns()
            if preview:
                cv2.imshow(WIN_NAME, frame)
                cv2.waitKey(1)
            tg1 = time.perf_counter_ns()
            t_gui_ms.append((tg1 - tg0) / 1e6)

            t_f1 = time.perf_counter_ns()
            t_frame_latencies_ms.append((t_f1 - t_f0) / 1e6)

            if frame_idx % 30 == 0:
                cpu_measurements.append(proc.cpu_percent())
                ram_measurements.append(proc.memory_info().rss / (1024 * 1024))

            frame_idx += 1

    t_wall_end = time.perf_counter()
    cap.release()
    if preview:
        cv2.destroyAllWindows()

    total_wall_s = t_wall_end - t_wall_start
    loop_fps = frame_idx / total_wall_s if total_wall_s > 0 else 0.0

    return {
        "frames_processed": frame_idx,
        "wall_clock_time_s": total_wall_s,
        "loop_throughput_fps": loop_fps,
        "total_frame_latency": compute_stats(t_frame_latencies_ms),
        "mediapipe_latency": compute_stats(t_inference_ms),
        "gesture_latency": compute_stats(t_gesture_ms),
        "tracker_latency": compute_stats(t_tracker_ms),
        "render_latency": compute_stats(t_render_ms),
        "gui_latency": compute_stats(t_gui_ms),
        "proc_cpu_mean": float(np.mean(cpu_measurements)) if cpu_measurements else 0.0,
        "proc_cpu_p95": float(np.percentile(cpu_measurements, 95)) if cpu_measurements else 0.0,
        "ram_mean_mb": float(np.mean(ram_measurements)) if ram_measurements else 0.0,
        "ram_peak_mb": float(np.max(ram_measurements)) if ram_measurements else 0.0,
        "hands_detected_by_seg": hands_detected_by_seg,
        "gestures_detected_by_seg": gestures_detected_by_seg,
        "actions_dispatched_by_seg": actions_dispatched_by_seg,
    }


def run_ab_preview():
    print("\n========================================================")
    print("      A/B EXPERIMENT 1: PREVIEW ON vs PREVIEW OFF       ")
    print("========================================================")
    print("Running Preview ON...")
    res_on = run_pipeline_on_video(preview=True)
    print(f"  Preview ON : {res_on['loop_throughput_fps']:.1f} FPS, frame_mean={res_on['total_frame_latency']['mean']:.2f}ms, CPU={res_on['proc_cpu_mean']:.1f}%")

    print("Running Preview OFF...")
    res_off = run_pipeline_on_video(preview=False)
    print(f"  Preview OFF: {res_off['loop_throughput_fps']:.1f} FPS, frame_mean={res_off['total_frame_latency']['mean']:.2f}ms, CPU={res_off['proc_cpu_mean']:.1f}%")

    fps_diff = res_off['loop_throughput_fps'] - res_on['loop_throughput_fps']
    lat_diff = res_off['total_frame_latency']['mean'] - res_on['total_frame_latency']['mean']
    cpu_diff = res_off['proc_cpu_mean'] - res_on['proc_cpu_mean']

    output = {
        "preview_on": res_on,
        "preview_off": res_off,
        "diff": {
            "fps_gain": fps_diff,
            "latency_reduction_ms": -lat_diff,
            "cpu_reduction_pct": -cpu_diff,
        },
        "verdict": "KEEP" if fps_diff > 0 else "REJECT",
    }
    with open("ab_preview.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
    print("Saved ab_preview.json")
    return output


def run_ab_num_hands():
    print("\n========================================================")
    print("      A/B EXPERIMENT 2: num_hands=1 vs num_hands=2      ")
    print("========================================================")
    print("Running num_hands=1...")
    res_1 = run_pipeline_on_video(preview=False, num_hands=1)
    print(f"  num_hands=1: {res_1['loop_throughput_fps']:.1f} FPS, MP_mean={res_1['mediapipe_latency']['mean']:.2f}ms, Seg7 hands={res_1['hands_detected_by_seg'][7]}")

    print("Running num_hands=2...")
    res_2 = run_pipeline_on_video(preview=False, num_hands=2)
    print(f"  num_hands=2: {res_2['loop_throughput_fps']:.1f} FPS, MP_mean={res_2['mediapipe_latency']['mean']:.2f}ms, Seg7 hands={res_2['hands_detected_by_seg'][7]}")

    output = {
        "num_hands_1": res_1,
        "num_hands_2": res_2,
        "diff": {
            "mediapipe_mean_diff_ms": res_2['mediapipe_latency']['mean'] - res_1['mediapipe_latency']['mean'],
            "seg7_hands_detected_1": res_1['hands_detected_by_seg'][7],
            "seg7_hands_detected_2": res_2['hands_detected_by_seg'][7],
        },
        "verdict": "KEEP num_hands=2" if res_2['hands_detected_by_seg'][7] > res_1['hands_detected_by_seg'][7] else "KEEP num_hands=1",
    }
    with open("ab_num_hands.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
    print("Saved ab_num_hands.json")
    return output


def run_ab_running_mode():
    print("\n========================================================")
    print("   A/B EXPERIMENT 3: RunningMode VIDEO vs LIVE_STREAM   ")
    print("========================================================")
    print("Running RunningMode.VIDEO...")
    res_video = run_pipeline_on_video(preview=False, running_mode=_RunningMode.VIDEO)
    print(f"  VIDEO      : {res_video['loop_throughput_fps']:.1f} FPS, MP_mean={res_video['mediapipe_latency']['mean']:.2f}ms")

    print("Running RunningMode.LIVE_STREAM...")
    res_live = run_pipeline_on_video(preview=False, running_mode=_RunningMode.LIVE_STREAM)
    print(f"  LIVE_STREAM: {res_live['loop_throughput_fps']:.1f} FPS, MP_mean={res_live['mediapipe_latency']['mean']:.2f}ms")

    output = {
        "mode_video": res_video,
        "mode_live_stream": res_live,
        "verdict": "KEEP VIDEO" if res_video['total_frame_latency']['mean'] <= res_live['total_frame_latency']['mean'] else "KEEP LIVE_STREAM",
    }
    with open("ab_running_mode.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
    print("Saved ab_running_mode.json")
    return output


def run_ab_resolution():
    print("\n========================================================")
    print("   A/B EXPERIMENT 4: INFERENCE RESOLUTION COMPARISON    ")
    print("========================================================")
    resolutions = [
        ("1280x720", 1280, 720),
        ("960x540", 960, 540),
        ("640x360", 640, 360),
    ]
    res_data = {}
    for name, w, h in resolutions:
        print(f"Testing Inference Resolution {name}...")
        res = run_pipeline_on_video(preview=False, infer_width=w, infer_height=h)
        res_data[name] = res
        print(f"  {name}: {res['loop_throughput_fps']:.1f} FPS, MP_mean={res['mediapipe_latency']['mean']:.2f}ms, MP_p95={res['mediapipe_latency']['p95']:.2f}ms, Seg8 (Far Hand)={res['hands_detected_by_seg'][8]}")

    output = {
        "resolutions": res_data,
        "summary_table": {
            name: {
                "mp_mean_ms": res_data[name]["mediapipe_latency"]["mean"],
                "mp_p95_ms": res_data[name]["mediapipe_latency"]["p95"],
                "total_mean_ms": res_data[name]["total_frame_latency"]["mean"],
                "throughput_fps": res_data[name]["loop_throughput_fps"],
                "proc_cpu_mean": res_data[name]["proc_cpu_mean"],
                "seg8_far_hands": res_data[name]["hands_detected_by_seg"][8],
                "seg6_fast_motion_hands": res_data[name]["hands_detected_by_seg"][6],
            }
            for name, _, _ in resolutions
        }
    }
    with open("ab_resolution.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
    print("Saved ab_resolution.json")
    return output


def run_ab_threaded_capture():
    print("\n========================================================")
    print("    A/B EXPERIMENT 5: THREADED CAPTURE EVALUATION       ")
    print("========================================================")
    # Test on live camera: synchronous read vs dedicated background reader thread
    from queue import Queue
    
    # 1. Synchronous
    print("Testing Synchronous Capture on camera (100 frames)...")
    cap_sync = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    cap_sync.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap_sync.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    cap_sync.set(cv2.CAP_PROP_FPS, 30)
    
    sync_read_times = []
    sync_frame_intervals = []
    t_last = time.perf_counter()
    for _ in range(100):
        t0 = time.perf_counter()
        ret, frame = cap_sync.read()
        t1 = time.perf_counter()
        if ret:
            sync_read_times.append((t1 - t0) * 1000.0)
            sync_frame_intervals.append((t1 - t_last) * 1000.0)
            t_last = t1
            # Simulate pipeline processing delay of 20ms
            time.sleep(0.020)
    cap_sync.release()
    
    # 2. Threaded Latest-Frame Reader
    print("Testing Threaded Latest-Frame Reader on camera (100 frames)...")
    class ThreadedCapture:
        def __init__(self, index=0, backend=cv2.CAP_DSHOW, width=1280, height=720, fps=30):
            self.cap = cv2.VideoCapture(index, backend)
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
            self.cap.set(cv2.CAP_PROP_FPS, fps)
            self.latest_frame = None
            self.latest_timestamp = 0.0
            self.lock = threading.Lock()
            self.running = True
            self.thread = threading.Thread(target=self._reader, daemon=True)
            self.thread.start()
            
        def _reader(self):
            while self.running:
                ret, frame = self.cap.read()
                if ret:
                    now = time.perf_counter()
                    with self.lock:
                        self.latest_frame = frame
                        self.latest_timestamp = now
                else:
                    time.sleep(0.005)
                    
        def read(self):
            with self.lock:
                if self.latest_frame is None:
                    return False, None, 0.0
                age_ms = (time.perf_counter() - self.latest_timestamp) * 1000.0
                return True, self.latest_frame, age_ms
                
        def release(self):
            self.running = False
            self.thread.join(timeout=1.0)
            self.cap.release()

    threaded_cap = ThreadedCapture()
    time.sleep(1.0) # Warm up thread
    
    thread_read_times = []
    thread_frame_ages = []
    t_last = time.perf_counter()
    for _ in range(100):
        t0 = time.perf_counter()
        ret, frame, age = threaded_cap.read()
        t1 = time.perf_counter()
        if ret:
            thread_read_times.append((t1 - t0) * 1000.0)
            thread_frame_ages.append(age)
            # Simulate pipeline processing delay of 20ms
            time.sleep(0.020)
    threaded_cap.release()

    sync_stats = {
        "read_latency_ms": compute_stats(sync_read_times),
        "frame_interval_ms": compute_stats(sync_frame_intervals),
        "effective_fps": 1000.0 / np.mean(sync_frame_intervals) if sync_frame_intervals else 0.0,
    }
    thread_stats = {
        "read_latency_ms": compute_stats(thread_read_times),
        "frame_age_ms": compute_stats(thread_frame_ages),
    }

    output = {
        "sync_capture": sync_stats,
        "threaded_capture": thread_stats,
        "analysis": {
            "sync_read_mean_ms": sync_stats["read_latency_ms"]["mean"],
            "thread_read_mean_ms": thread_stats["read_latency_ms"]["mean"],
            "thread_frame_age_mean_ms": thread_stats["frame_age_ms"]["mean"],
            "thread_frame_age_p95_ms": thread_stats["frame_age_ms"]["p95"],
        }
    }
    with open("ab_threaded_capture.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
    print("Saved ab_threaded_capture.json")
    return output


def run_all_ab_experiments():
    print("\n========================================================")
    print("         RUNNING FULL A/B BENCHMARK SUITE                ")
    print("========================================================")
    run_ab_preview()
    run_ab_num_hands()
    run_ab_running_mode()
    run_ab_resolution()
    run_ab_threaded_capture()
    print("\nAll A/B experiments completed successfully!")


if __name__ == "__main__":
    run_all_ab_experiments()

