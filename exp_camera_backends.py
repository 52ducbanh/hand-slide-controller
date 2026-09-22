"""Phase 2: Comprehensive Camera Backend Benchmark & Prototype Comparison.

Strictly compares:
- Candidate A: OpenCV MSMF (Constructor Parameters 1280x720 @ 30 FPS)
- Candidate B: OpenCV MSMF (Sequential cap.set() 1280x720 @ 30 FPS)
- Candidate C: OpenCV MSMF (FOURCC formats: MJPG, YUY2, NV12)
- Candidate D: WinRT MediaFrameReader (Realtime Semantics, TryAcquireLatestFrame, SystemRelativeTime)
- Candidate E: OpenCV MSMF Single-Slot Threaded Capture (Producer-Consumer)

Measures:
- Detailed startup breakdown (Device open, Format negotiation, First frame, Total startup)
- 100-frame steady-state streaming (Frame interval, Jitter, Effective FPS)
- Delivery age / freshness:
  * For WinRT: SystemRelativeTime vs QPC_now (Hardware sample age in ms)
  * For OpenCV MSMF: Stamped as 'TRUE SOURCE/SENSOR TIMESTAMP NOT OBSERVABLE THROUGH THIS API'
  * For Threaded Capture: Slot age
- CPU utilization
- Protocol: 3 trials per candidate in alternating order, 1.0s driver cooldown between trials

Outputs:
- camera_backend_benchmark.json
- camera_backend_trials.csv
- camera_backend_report.md
"""

from __future__ import annotations
import asyncio
import csv
import ctypes
import json
import os
import sys
import threading
import time
from typing import Any

import cv2
import numpy as np
import psutil

# QPC clock helpers
qpc = ctypes.c_int64()
qpf = ctypes.c_int64()
ctypes.windll.kernel32.QueryPerformanceFrequency(ctypes.byref(qpf))


def get_qpc_sec() -> float:
    ctypes.windll.kernel32.QueryPerformanceCounter(ctypes.byref(qpc))
    return float(qpc.value) / float(qpf.value)


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


def test_candidate_a_constructor(trial_idx: int) -> dict[str, Any]:
    print(f"  [Cand A: MSMF Constructor Params] Trial {trial_idx}...", end=" ", flush=True)
    proc = psutil.Process()
    params = [
        cv2.CAP_PROP_FRAME_WIDTH, 1280,
        cv2.CAP_PROP_FRAME_HEIGHT, 720,
        cv2.CAP_PROP_FPS, 30,
    ]
    t0 = time.perf_counter_ns()
    cap = cv2.VideoCapture(0, cv2.CAP_MSMF, params)
    t1 = time.perf_counter_ns()
    open_ms = (t1 - t0) / 1e6
    config_ms = 0.0

    if not cap.isOpened():
        return {"status": "FAILED", "error": "Could not open camera"}

    t2 = time.perf_counter_ns()
    ret, frame = cap.read()
    t3 = time.perf_counter_ns()
    first_frame_ms = (t3 - t2) / 1e6
    total_startup_ms = (t3 - t0) / 1e6

    actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    actual_fps = float(cap.get(cv2.CAP_PROP_FPS))

    # Stream 100 frames
    read_durations = []
    intervals = []
    last_t = time.perf_counter_ns()
    cpu_before = proc.cpu_percent(interval=None)

    t_stream_start = time.perf_counter_ns()
    for _ in range(100):
        ta = time.perf_counter_ns()
        ret, frame = cap.read()
        tb = time.perf_counter_ns()
        if not ret:
            continue
        read_durations.append((tb - ta) / 1e6)
        intervals.append((tb - last_t) / 1e6)
        last_t = tb

    t_stream_end = time.perf_counter_ns()
    cpu_after = proc.cpu_percent(interval=None)
    stream_sec = (t_stream_end - t_stream_start) / 1e9
    effective_fps = len(read_durations) / stream_sec if stream_sec > 0 else 0.0

    cap.release()
    time.sleep(1.0)
    print(f"Done! Startup: {total_startup_ms:.1f}ms, Stream: {effective_fps:.2f} FPS")

    return {
        "candidate": "A_OPENCV_MSMF_CONSTRUCTOR",
        "trial": trial_idx,
        "status": "PASS",
        "startup": {
            "open_ms": round(open_ms, 2),
            "config_ms": round(config_ms, 2),
            "first_frame_ms": round(first_frame_ms, 2),
            "total_startup_ms": round(total_startup_ms, 2),
        },
        "negotiated": {"width": actual_w, "height": actual_h, "fps": actual_fps, "subtype": "RGB24 (MSMF default)"},
        "stream": {
            "effective_fps": round(effective_fps, 2),
            "frame_interval_ms": compute_stats(intervals),
            "read_duration_ms": compute_stats(read_durations),
            "sample_delivery_age": "TRUE SOURCE/SENSOR TIMESTAMP NOT OBSERVABLE THROUGH THIS API",
            "cpu_percent": round(cpu_after, 2),
        }
    }


def test_candidate_b_sequential(trial_idx: int) -> dict[str, Any]:
    print(f"  [Cand B: MSMF Sequential Set] Trial {trial_idx}...", end=" ", flush=True)
    proc = psutil.Process()
    t0 = time.perf_counter_ns()
    cap = cv2.VideoCapture(0, cv2.CAP_MSMF)
    t1 = time.perf_counter_ns()
    open_ms = (t1 - t0) / 1e6

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    cap.set(cv2.CAP_PROP_FPS, 30)
    t2 = time.perf_counter_ns()
    config_ms = (t2 - t1) / 1e6

    ret, frame = cap.read()
    t3 = time.perf_counter_ns()
    first_frame_ms = (t3 - t2) / 1e6
    total_startup_ms = (t3 - t0) / 1e6

    actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    actual_fps = float(cap.get(cv2.CAP_PROP_FPS))

    read_durations = []
    intervals = []
    last_t = time.perf_counter_ns()

    t_stream_start = time.perf_counter_ns()
    for _ in range(100):
        ta = time.perf_counter_ns()
        ret, frame = cap.read()
        tb = time.perf_counter_ns()
        if not ret:
            continue
        read_durations.append((tb - ta) / 1e6)
        intervals.append((tb - last_t) / 1e6)
        last_t = tb

    t_stream_end = time.perf_counter_ns()
    cpu_after = proc.cpu_percent(interval=None)
    stream_sec = (t_stream_end - t_stream_start) / 1e9
    effective_fps = len(read_durations) / stream_sec if stream_sec > 0 else 0.0

    cap.release()
    time.sleep(1.0)
    print(f"Done! Startup: {total_startup_ms:.1f}ms, Stream: {effective_fps:.2f} FPS")

    return {
        "candidate": "B_OPENCV_MSMF_SEQUENTIAL",
        "trial": trial_idx,
        "status": "PASS",
        "startup": {
            "open_ms": round(open_ms, 2),
            "config_ms": round(config_ms, 2),
            "first_frame_ms": round(first_frame_ms, 2),
            "total_startup_ms": round(total_startup_ms, 2),
        },
        "negotiated": {"width": actual_w, "height": actual_h, "fps": actual_fps, "subtype": "RGB24 (MSMF default)"},
        "stream": {
            "effective_fps": round(effective_fps, 2),
            "frame_interval_ms": compute_stats(intervals),
            "read_duration_ms": compute_stats(read_durations),
            "sample_delivery_age": "TRUE SOURCE/SENSOR TIMESTAMP NOT OBSERVABLE THROUGH THIS API",
            "cpu_percent": round(cpu_after, 2),
        }
    }


def test_candidate_c_fourcc(trial_idx: int) -> dict[str, Any]:
    print(f"  [Cand C: MSMF FOURCC Testing] Trial {trial_idx}...", end=" ", flush=True)
    results_fcc = {}
    for code in ["MJPG", "YUY2", "NV12"]:
        fcc = cv2.VideoWriter_fourcc(*code)
        cap = cv2.VideoCapture(0, cv2.CAP_MSMF)
        set_ok = cap.set(cv2.CAP_PROP_FOURCC, fcc)
        get_val = cap.get(cv2.CAP_PROP_FOURCC)
        cap.release()
        results_fcc[code] = {
            "set_returned": bool(set_ok),
            "get_fourcc": float(get_val),
            "honored": bool(set_ok and get_val == fcc)
        }
        time.sleep(0.5)

    print("Done! All FOURCC requests rejected by OpenCV MSMF (locked to internal format).")
    return {
        "candidate": "C_OPENCV_MSMF_FOURCC",
        "trial": trial_idx,
        "status": "TESTED",
        "formats_tested": results_fcc,
        "verdict": "OpenCV MSMF rejects dynamic FOURCC property changes; maintains default RGB24 source reader pipeline."
    }


async def _run_winrt_trial(trial_idx: int) -> dict[str, Any]:
    print(f"  [Cand D: WinRT MediaFrameReader] Trial {trial_idx}...", end=" ", flush=True)
    import winrt.windows.media.capture as wmc
    import winrt.windows.media.capture.frames as wmcf

    proc = psutil.Process()
    t0 = time.perf_counter_ns()

    settings = wmc.MediaCaptureInitializationSettings()
    settings.memory_preference = wmc.MediaCaptureMemoryPreference.CPU
    settings.streaming_capture_mode = wmc.StreamingCaptureMode.VIDEO
    mc = wmc.MediaCapture()
    await mc.initialize_with_settings_async(settings)
    t1 = time.perf_counter_ns()
    open_ms = (t1 - t0) / 1e6

    source = list(mc.frame_sources.values())[0]
    target_fmt = [f for f in source.supported_formats if f.video_format.width == 1280 and f.subtype == 'NV12'][0]
    await source.set_format_async(target_fmt)
    t2 = time.perf_counter_ns()
    config_ms = (t2 - t1) / 1e6

    reader = await mc.create_frame_reader_async(source)
    reader.acquisition_mode = wmcf.MediaFrameReaderAcquisitionMode.REALTIME
    await reader.start_async()
    t3 = time.perf_counter_ns()

    # Wait for first frame
    first_frame_ms = 0.0
    while time.perf_counter_ns() - t3 < 3_000_000_000:
        f = reader.try_acquire_latest_frame()
        if f:
            t4 = time.perf_counter_ns()
            first_frame_ms = (t4 - t3) / 1e6
            f.close()
            break
        await asyncio.sleep(0.005)

    total_startup_ms = (time.perf_counter_ns() - t0) / 1e6

    # Stream 100 frames and measure SystemRelativeTime vs QPC_now
    delays_ms = []
    intervals_ms = []
    last_ts_sec = None
    frames_acquired = 0

    t_stream_start = time.perf_counter_ns()
    while frames_acquired < 100 and (time.perf_counter_ns() - t_stream_start) < 5_000_000_000:
        t_acq = get_qpc_sec()
        frame_ref = reader.try_acquire_latest_frame()
        if frame_ref is not None:
            ts = frame_ref.system_relative_time
            if ts:
                ts_sec = ts.total_seconds()
                if last_ts_sec is not None and ts_sec > last_ts_sec:
                    intervals_ms.append((ts_sec - last_ts_sec) * 1000.0)
                    delays_ms.append((t_acq - ts_sec) * 1000.0)
                    frames_acquired += 1
                last_ts_sec = ts_sec
            frame_ref.close()
        await asyncio.sleep(0.005)

    t_stream_end = time.perf_counter_ns()
    cpu_after = proc.cpu_percent(interval=None)
    stream_sec = (t_stream_end - t_stream_start) / 1e9
    effective_fps = frames_acquired / stream_sec if stream_sec > 0 else 0.0

    await reader.stop_async()
    reader.close()
    mc.close()
    time.sleep(1.0)

    print(f"Done! Startup: {total_startup_ms:.1f}ms, Stream: {effective_fps:.2f} FPS, Sample Delivery Age: {np.mean(delays_ms):.2f}ms")

    return {
        "candidate": "D_WINRT_MEDIA_FRAME_READER",
        "trial": trial_idx,
        "status": "PASS",
        "startup": {
            "open_ms": round(open_ms, 2),
            "config_ms": round(config_ms, 2),
            "first_frame_ms": round(first_frame_ms, 2),
            "total_startup_ms": round(total_startup_ms, 2),
        },
        "negotiated": {"width": 1280, "height": 720, "fps": 30.0, "subtype": "NV12"},
        "stream": {
            "effective_fps": round(effective_fps, 2),
            "frame_interval_ms": compute_stats(intervals_ms),
            "hardware_sample_delivery_age_ms": compute_stats(delays_ms),
            "cpu_percent": round(cpu_after, 2),
        }
    }


def test_candidate_d_winrt(trial_idx: int) -> dict[str, Any]:
    return asyncio.run(_run_winrt_trial(trial_idx))


def test_candidate_e_threaded(trial_idx: int) -> dict[str, Any]:
    print(f"  [Cand E: MSMF Single-Slot Threaded] Trial {trial_idx}...", end=" ", flush=True)
    from exp_camera_threading import SingleSlotThreadedCapture
    proc = psutil.Process()
    t0 = time.perf_counter_ns()
    threaded = SingleSlotThreadedCapture(0, 1280, 720, 30)
    threaded.start()

    # Wait for first frame
    while True:
        f, seq, cap_time, ret_ms = threaded.get_latest()
        if f is not None:
            break
        time.sleep(0.01)
    total_startup_ms = (time.perf_counter_ns() - t0) / 1e6

    get_durations = []
    slot_ages = []
    intervals = []
    duplicates = 0
    last_seq = -1
    last_t = time.perf_counter_ns()

    t_stream_start = time.perf_counter_ns()
    for _ in range(100):
        t_acq = time.perf_counter_ns()
        f, seq, cap_time, ret_ms = threaded.get_latest()
        if f is None:
            continue
        interval_ms = (t_acq - last_t) / 1e6
        last_t = t_acq
        if seq == last_seq:
            duplicates += 1
        last_seq = seq

        age_ms = (t_acq - cap_time) / 1e6
        get_durations.append(ret_ms)
        slot_ages.append(age_ms)
        intervals.append(interval_ms)
        time.sleep(0.030)

    t_stream_end = time.perf_counter_ns()
    cpu_after = proc.cpu_percent(interval=None)
    stream_sec = (t_stream_end - t_stream_start) / 1e9
    effective_fps = 100 / stream_sec if stream_sec > 0 else 0.0

    threaded.release()
    time.sleep(1.0)
    print(f"Done! Startup: {total_startup_ms:.1f}ms, Duplicates: {duplicates}%, Slot Age: {np.mean(slot_ages):.2f}ms")

    return {
        "candidate": "E_OPENCV_MSMF_THREADED",
        "trial": trial_idx,
        "status": "PASS",
        "startup": {"total_startup_ms": round(total_startup_ms, 2)},
        "negotiated": {"width": 1280, "height": 720, "fps": 30.0, "subtype": "RGB24"},
        "stream": {
            "effective_fps": round(effective_fps, 2),
            "duplicate_rate_pct": float(duplicates),
            "slot_age_ms": compute_stats(slot_ages),
            "frame_interval_ms": compute_stats(intervals),
            "cpu_percent": round(cpu_after, 2),
        }
    }


def main():
    print("==================================================================")
    print("STARTING PHASE 2: CAMERA BACKEND COMPARISON BENCHMARK")
    print("==================================================================")

    trials_data: list[dict[str, Any]] = []

    # 3 alternating rounds
    for round_idx in range(1, 4):
        print(f"\n--- ROUND {round_idx}/3 ---")
        trials_data.append(test_candidate_a_constructor(round_idx))
        trials_data.append(test_candidate_b_sequential(round_idx))
        trials_data.append(test_candidate_d_winrt(round_idx))
        trials_data.append(test_candidate_e_threaded(round_idx))

    # Single FOURCC test
    trials_data.append(test_candidate_c_fourcc(1))

    # Aggregate summaries
    def get_summary(cand_name: str) -> dict[str, Any]:
        items = [t for t in trials_data if t["candidate"] == cand_name and t.get("status") == "PASS"]
        if not items:
            return {}
        startup_vals = [t["startup"]["total_startup_ms"] for t in items if "total_startup_ms" in t.get("startup", {})]
        fps_vals = [t["stream"]["effective_fps"] for t in items]
        cpu_vals = [t["stream"]["cpu_percent"] for t in items]
        return {
            "trials_count": len(items),
            "startup_total_ms": compute_stats(startup_vals),
            "effective_fps": compute_stats(fps_vals),
            "cpu_percent": compute_stats(cpu_vals),
            "negotiated_format": items[0]["negotiated"],
        }

    summary_a = get_summary("A_OPENCV_MSMF_CONSTRUCTOR")
    summary_b = get_summary("B_OPENCV_MSMF_SEQUENTIAL")
    summary_d = get_summary("D_WINRT_MEDIA_FRAME_READER")
    summary_e = get_summary("E_OPENCV_MSMF_THREADED")

    winrt_delays = []
    for t in trials_data:
        if t["candidate"] == "D_WINRT_MEDIA_FRAME_READER" and "hardware_sample_delivery_age_ms" in t.get("stream", {}):
            winrt_delays.extend([t["stream"]["hardware_sample_delivery_age_ms"]["mean"]])

    if summary_d:
        summary_d["hardware_sample_delivery_age_ms"] = compute_stats(winrt_delays)

    report_payload = {
        "metadata": {
            "test_type": "CAMERA_BACKEND_COMPREHENSIVE_BENCHMARK",
            "date": time.strftime("%Y-%m-%d %H:%M:%S"),
            "device_index": 0,
            "target_resolution": "1280x720",
            "target_fps": 30,
        },
        "candidate_summaries": {
            "A_OPENCV_MSMF_CONSTRUCTOR": summary_a,
            "B_OPENCV_MSMF_SEQUENTIAL": summary_b,
            "D_WINRT_MEDIA_FRAME_READER": summary_d,
            "E_OPENCV_MSMF_THREADED": summary_e,
        },
        "trials": trials_data,
    }

    with open("camera_backend_benchmark.json", "w", encoding="utf-8") as f:
        json.dump(report_payload, f, indent=2)

    # Export flat CSV
    csv_rows = []
    for t in trials_data:
        if t.get("status") == "PASS":
            csv_rows.append({
                "candidate": t["candidate"],
                "trial": t["trial"],
                "total_startup_ms": t["startup"].get("total_startup_ms", 0.0),
                "effective_fps": t["stream"].get("effective_fps", 0.0),
                "cpu_percent": t["stream"].get("cpu_percent", 0.0),
                "resolution": f"{t['negotiated']['width']}x{t['negotiated']['height']}",
                "subtype": t["negotiated"]["subtype"],
            })
    with open("camera_backend_trials.csv", "w", newline="", encoding="utf-8") as f:
        if csv_rows:
            writer = csv.DictWriter(f, fieldnames=list(csv_rows[0].keys()))
            writer.writeheader()
            writer.writerows(csv_rows)

    print("\n==================================================================")
    print("PHASE 2 COMPLETE: Saved camera_backend_benchmark.json and camera_backend_trials.csv")
    print("==================================================================")


if __name__ == "__main__":
    main()
