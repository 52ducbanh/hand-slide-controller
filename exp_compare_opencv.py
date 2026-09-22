import sys
import os
import time
import json
import numpy as np
import cv2
import mediapipe as mp

from hand_controller.config import AppConfig, CameraConfig
from hand_controller.camera import create_camera_source, OpenCVMSMFCameraSource, WinRTCameraSource

def test_nv12_conversion_latency(n=1000):
    h, w = 720, 1280
    nv12_data = np.zeros((h * 3 // 2, w), dtype=np.uint8)
    rgb_slot = np.empty((h, w, 3), dtype=np.uint8)

    # Warmup
    for _ in range(50):
        cv2.cvtColor(nv12_data, cv2.COLOR_YUV2RGB_NV12, dst=rgb_slot)
        cv2.flip(rgb_slot, 1, dst=rgb_slot)

    latencies_us = []
    for _ in range(n):
        t0 = time.perf_counter_ns()
        cv2.cvtColor(nv12_data, cv2.COLOR_YUV2RGB_NV12, dst=rgb_slot)
        cv2.flip(rgb_slot, 1, dst=rgb_slot)
        t1 = time.perf_counter_ns()
        latencies_us.append((t1 - t0) / 1000.0)

    s = sorted(latencies_us)
    return {
        "iterations": n,
        "mean_us": round(sum(s) / len(s), 2),
        "p50_us": round(s[len(s) // 2], 2),
        "p90_us": round(s[int(len(s) * 0.90)], 2),
        "p95_us": round(s[min(len(s)-1, int(len(s) * 0.95))], 2),
        "p99_us": round(s[min(len(s)-1, int(len(s) * 0.99))], 2),
    }

def test_mediapipe_compatibility():
    cfg = AppConfig()
    options = mp.tasks.vision.HandLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=cfg.model_path),
        running_mode=mp.tasks.vision.RunningMode.IMAGE,
        num_hands=2,
    )
    with mp.tasks.vision.HandLandmarker.create_from_options(options) as landmarker:
        test_img = np.zeros((720, 1280, 3), dtype=np.uint8)
        mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=test_img)
        res = landmarker.detect(mp_img)
        return {"status": "PASS", "detected_hands": len(res.hand_landmarks) if res.hand_landmarks else 0}

def test_camera_backends():
    results = {}
    # WinRT test
    try:
        t0 = time.perf_counter()
        cfg_winrt = CameraConfig(backend="WINRT")
        cam = create_camera_source(cfg_winrt)
        t_open = time.perf_counter() - t0
        success, frame = cam.read_latest(timeout_sec=1.5)
        t_close0 = time.perf_counter()
        cam.close()
        t_close = time.perf_counter() - t_close0
        results["winrt"] = {
            "status": "PASS" if success else "FAIL",
            "frame_shape": list(frame.image_rgb.shape) if frame else None,
            "open_sec": round(t_open, 3),
            "close_sec": round(t_close, 3)
        }
    except Exception as e:
        results["winrt"] = {"status": "ERROR", "error": str(e)}

    # MSMF forced fallback test
    try:
        t0 = time.perf_counter()
        cfg_msmf = CameraConfig(backend="AUTO", force_winrt_init_failure=True)
        cam_msmf = create_camera_source(cfg_msmf)
        t_open = time.perf_counter() - t0
        success, frame = cam_msmf.read_latest(timeout_sec=2.0)
        t_close0 = time.perf_counter()
        cam_msmf.close()
        t_close = time.perf_counter() - t_close0
        results["msmf_fallback"] = {
            "status": "PASS" if success else "FAIL",
            "frame_shape": list(frame.image_rgb.shape) if frame else None,
            "open_sec": round(t_open, 3),
            "close_sec": round(t_close, 3)
        }
    except Exception as e:
        results["msmf_fallback"] = {"status": "ERROR", "error": str(e)}

    return results

if __name__ == "__main__":
    print("Running OpenCV comparison benchmark in python:", sys.executable)
    print("OpenCV version:", cv2.__version__)
    conv = test_nv12_conversion_latency(1000)
    print("NV12->RGB conversion latency:", conv)
    mp_test = test_mediapipe_compatibility()
    print("MediaPipe compatibility:", mp_test)
    cams = test_camera_backends()
    print("Camera backends test:", cams)

    out = {
        "python": sys.executable,
        "cv2_version": cv2.__version__,
        "cv2_file": cv2.__file__,
        "cv2_pyd_size_bytes": os.path.getsize(cv2.__file__),
        "cv2_pyd_size_mb": round(os.path.getsize(cv2.__file__) / (1024*1024), 2),
        "conversion_latency": conv,
        "mediapipe": mp_test,
        "cameras": cams
    }
    with open("opencv_exp_benchmark.json", "w") as f:
        json.dump(out, f, indent=2)
    print("Saved opencv_exp_benchmark.json")
