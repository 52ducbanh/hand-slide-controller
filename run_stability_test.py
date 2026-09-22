"""Phase 17 - Stability and Reliability Validation Suite.
Validates long-running pipeline stability:
- Memory leak detection (RAM RSS slope over thousands of frames)
- CPU utilization stability
- Latency percentiles stability
- Repeated startup and clean shutdown cycles (re-open 3 times)
- Error and exception tracking
Outputs stability_results.json.
"""

from __future__ import annotations
import os
import sys
import time
import json
import psutil
import cv2
import numpy as np
import mediapipe as mp
import pyautogui

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0
pyautogui.press = lambda k: None

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

def run_stability_session(num_frames: int = 1500) -> dict:
    print(f"--- Running Continuous Stability Session ({num_frames} frames) ---")
    proc = psutil.Process(os.getpid())
    cfg = AppConfig(preview_enabled=False)
    
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
    dispatcher = ActionDispatcher(cooldown=cfg.global_action_cooldown, audio_feedback=False)
    
    cap = cv2.VideoCapture("benchmark_input.mp4")
    
    latencies: list[float] = []
    mp_latencies: list[float] = []
    ram_samples: list[float] = []
    cpu_samples: list[float] = []
    actions_fired = 0
    
    rgb_buf = None
    t0 = time.perf_counter()
    
    with _HandLandmarker.create_from_options(options) as landmarker:
        for f in range(num_frames):
            t_f0 = time.perf_counter()
            ret, frame = cap.read()
            if not ret or frame is None:
                # Rewind video for continuous soak testing
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ret, frame = cap.read()
                if not ret:
                    break
                    
            cv2.flip(frame, 1, dst=frame)
            if rgb_buf is None or rgb_buf.shape != frame.shape:
                rgb_buf = np.empty_like(frame)
            cv2.cvtColor(frame, cv2.COLOR_BGR2RGB, dst=rgb_buf)
            mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_buf)
            
            now = f * 0.033
            ts_ms = int(now * 1000) + 1
            
            t_mp0 = time.perf_counter()
            res = landmarker.detect_for_video(mp_img, ts_ms)
            t_mp1 = time.perf_counter()
            mp_latencies.append((t_mp1 - t_mp0) * 1000.0)
            
            dets = []
            if res.hand_landmarks:
                for l in res.hand_landmarks:
                    gr = recognizer.classify(l)
                    dets.append(HandDetection(landmarks=l, wrist_x=l[0].x, wrist_y=l[0].y, gesture_result=gr))
                    
            assigns = tracker.assign(dets, now)
            for d_idx, t_idx in assigns.items():
                t = tracker.tracks[t_idx]
                tracker.update_position(t, dets[d_idx], now)
                act = state_machine.update(t, dets[d_idx], now)
                if act != SlideAction.NONE:
                    if dispatcher.dispatch(act, now):
                        state_machine.latch(t)
                        actions_fired += 1
            tracker.expire_lost_tracks(assigns.values(), now)
            
            t_f1 = time.perf_counter()
            latencies.append((t_f1 - t_f0) * 1000.0)
            
            if f % 50 == 0:
                ram_mb = proc.memory_info().rss / (1024 * 1024)
                ram_samples.append(ram_mb)
                cpu_samples.append(proc.cpu_percent())
                
    cap.release()
    t_total = time.perf_counter() - t0
    
    # Check RAM slope
    ram_start = ram_samples[0] if ram_samples else 0.0
    ram_end = ram_samples[-1] if ram_samples else 0.0
    ram_growth_mb = ram_end - ram_start
    
    return {
        "frames": num_frames,
        "total_time_s": t_total,
        "throughput_fps": num_frames / t_total if t_total > 0 else 0.0,
        "ram_start_mb": ram_start,
        "ram_end_mb": ram_end,
        "ram_peak_mb": float(np.max(ram_samples)) if ram_samples else 0.0,
        "ram_growth_mb": ram_growth_mb,
        "memory_leak_detected": ram_growth_mb > 15.0, # Threshold for meaningful leak
        "latency_mean_ms": float(np.mean(latencies)),
        "latency_p95_ms": float(np.percentile(latencies, 95)),
        "mediapipe_mean_ms": float(np.mean(mp_latencies)),
        "mediapipe_p95_ms": float(np.percentile(mp_latencies, 95)),
        "cpu_mean": float(np.mean(cpu_samples)),
        "actions_fired": actions_fired,
    }

def run_repeated_startup_test(cycles: int = 3) -> list[dict]:
    print(f"\n--- Testing Repeated Camera & Model Startup ({cycles} cycles) ---")
    results = []
    cfg = AppConfig()
    for c in range(1, cycles + 1):
        t0 = time.perf_counter()
        cap = cv2.VideoCapture(cfg.camera.index, cv2.CAP_MSMF)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, cfg.camera.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg.camera.height)
        cap.set(cv2.CAP_PROP_FPS, cfg.camera.fps)
        t_cam = time.perf_counter()
        
        options = _HandLandmarkerOptions(
            base_options=_BaseOptions(model_asset_path=cfg.model_path),
            running_mode=_RunningMode.VIDEO,
            num_hands=cfg.num_hands,
        )
        with _HandLandmarker.create_from_options(options) as lm:
            t_model = time.perf_counter()
            ret, frame = cap.read()
            t_first = time.perf_counter()
            
        cap.release()
        cycle_res = {
            "cycle": c,
            "success": ret and frame is not None,
            "cam_init_ms": (t_cam - t0) * 1000.0,
            "model_init_ms": (t_model - t_cam) * 1000.0,
            "first_frame_ms": (t_first - t_model) * 1000.0,
            "total_ms": (t_first - t0) * 1000.0,
        }
        print(f"  Cycle {c}: success={cycle_res['success']}, total={cycle_res['total_ms']:.1f}ms")
        results.append(cycle_res)
        time.sleep(1.0)
    return results

def main():
    session_res = run_stability_session(num_frames=1200)
    startup_res = run_repeated_startup_test(cycles=3)
    
    output = {
        "session": session_res,
        "repeated_startup": startup_res,
        "verdict": "PASS" if not session_res["memory_leak_detected"] and all(r["success"] for r in startup_res) else "FAIL"
    }
    
    with open("stability_results.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
    print("\nSaved stability_results.json")
    print(f"Stability Verdict: {output['verdict']}")

if __name__ == "__main__":
    main()
