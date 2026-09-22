"""Measure True Camera and Pipeline Startup Latency.

Separates:
- Trial 1: Cold start (Initial driver negotiation, device discovery, first model allocation)
- Trials 2-5: Warm / Repeated startups
"""

from __future__ import annotations
import json
import time
from typing import Any

import cv2
import mediapipe as mp

from hand_controller.config import AppConfig

_BaseOptions = mp.tasks.BaseOptions
_HandLandmarker = mp.tasks.vision.HandLandmarker
_HandLandmarkerOptions = mp.tasks.vision.HandLandmarkerOptions
_RunningMode = mp.tasks.vision.RunningMode


def measure_startup_trials(num_trials: int = 5) -> dict[str, Any]:
    print("\n=======================================================")
    print(f"MEASURING STARTUP LATENCY (Trial 1 = Cold, Trials 2-{num_trials} = Warm)")
    print("=======================================================")

    cfg = AppConfig()
    trials_data = []

    for trial_idx in range(1, num_trials + 1):
        is_cold = (trial_idx == 1)
        trial_type = "Cold (Initial)" if is_cold else f"Warm (Repeated #{trial_idx})"
        print(f"\nRunning Trial {trial_idx}: {trial_type}...")

        t_start = time.perf_counter()

        # Stage 1: Camera device creation & resolution negotiation
        t0 = time.perf_counter()
        cap = cv2.VideoCapture(cfg.camera.index, cv2.CAP_MSMF)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, cfg.camera.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg.camera.height)
        cap.set(cv2.CAP_PROP_FPS, cfg.camera.fps)
        t_cam = time.perf_counter()
        cam_init_ms = (t_cam - t0) * 1000.0

        # Stage 2: MediaPipe HandLandmarker creation & model delegate allocation
        options = _HandLandmarkerOptions(
            base_options=_BaseOptions(model_asset_path=cfg.model_path),
            running_mode=_RunningMode.LIVE_STREAM,
            num_hands=2,
            result_callback=lambda res, img, ts: None,
        )
        t1 = time.perf_counter()
        landmarker = _HandLandmarker.create_from_options(options)
        t_model = time.perf_counter()
        model_init_ms = (t_model - t1) * 1000.0

        # Stage 3: First physical camera frame capture
        t2 = time.perf_counter()
        ret, frame = cap.read()
        t_frame = time.perf_counter()
        first_frame_ms = (t_frame - t2) * 1000.0

        # Stage 4: First async inference submission
        first_infer_ms = 0.0
        if ret and frame is not None:
            cv2.flip(frame, 1, dst=frame)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            t3 = time.perf_counter()
            landmarker.detect_async(mp_image, 1)
            t4 = time.perf_counter()
            first_infer_ms = (t4 - t3) * 1000.0

        # Teardown
        landmarker.close()
        cap.release()
        t_end = time.perf_counter()
        total_trial_ms = (t_end - t_start) * 1000.0

        trial_record = {
            "trial": trial_idx,
            "type": "cold" if is_cold else "warm",
            "camera_init_ms": round(cam_init_ms, 2),
            "model_init_ms": round(model_init_ms, 2),
            "first_frame_read_ms": round(first_frame_ms, 2),
            "first_infer_submit_ms": round(first_infer_ms, 2),
            "total_startup_ms": round(total_trial_ms, 2),
            "success": ret,
        }
        trials_data.append(trial_record)
        print(f"  - Camera Init: {cam_init_ms:.1f} ms")
        print(f"  - Model Init:  {model_init_ms:.1f} ms")
        print(f"  - First Frame: {first_frame_ms:.1f} ms")
        print(f"  - Total:       {total_trial_ms:.1f} ms")

        # Small delay between warm runs to allow device handle release
        time.sleep(1.0)

    cold_trial = trials_data[0]
    warm_trials = trials_data[1:]

    warm_cam_mean = sum(t["camera_init_ms"] for t in warm_trials) / len(warm_trials)
    warm_total_mean = sum(t["total_startup_ms"] for t in warm_trials) / len(warm_trials)

    summary = {
        "cold_startup_trial_1": cold_trial,
        "warm_startups_trials_2_to_5": {
            "trials": warm_trials,
            "mean_camera_init_ms": round(warm_cam_mean, 2),
            "mean_total_startup_ms": round(warm_total_mean, 2),
        },
        "analysis": {
            "cold_vs_warm_ratio": round(cold_trial["total_startup_ms"] / warm_total_mean, 2) if warm_total_mean > 0 else 1.0,
            "root_cause_explanation": "Trial 1 undergoes full Windows Media Foundation device graph enumeration and hardware sensor exposure; subsequent warm runs reuse driver cache."
        }
    }

    with open("startup_benchmark.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print("\nSaved startup_benchmark.json successfully!")
    return summary


if __name__ == "__main__":
    measure_startup_trials(num_trials=5)
