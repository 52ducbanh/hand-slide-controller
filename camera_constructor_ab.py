"""Camera Hardware A/B Benchmark: Sequential set() vs Constructor Parameters.

Measures physical webcam (Index 0, cv2.CAP_MSMF) under:
- Condition A: Sequential set() (cv2.VideoCapture(0, cv2.CAP_MSMF) -> set W, H, FPS)
- Condition B: Constructor Parameters (cv2.VideoCapture(0, cv2.CAP_MSMF, [W, 1280, H, 720, FPS, 30]))

Protocol:
- 5 trials each, executed in strictly alternating order (A, B, A, B, A, B, A, B, A, B)
- 1.0 second cooldown between trials for Media Foundation driver handle cleanup
- Metrics per trial:
  * open_time_ms
  * config_time_ms
  * first_frame_time_ms
  * total_startup_ms
  * actual_width, actual_height, actual_fps
  * 20-second steady-state frame capture (frame count, interval stats, effective FPS, unique frames)
- Exports: camera_constructor_ab.json
"""

from __future__ import annotations
import json
import os
import sys
import time
from typing import Any

import cv2
import numpy as np


def compute_stats(vals: list[float]) -> dict[str, float]:
    if not vals:
        return {
            "samples": 0, "mean": 0.0, "median": 0.0, "p50": 0.0,
            "p90": 0.0, "p95": 0.0, "p99": 0.0, "min": 0.0, "max": 0.0, "std": 0.0
        }
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


def run_single_trial(condition: str, trial_num: int, duration_sec: float = 20.0) -> dict[str, Any]:
    print(f"\n--- Running Trial {trial_num} [{condition}] ---")
    cap = None
    try:
        t0 = time.perf_counter()
        if condition == "SEQUENTIAL_SET":
            cap = cv2.VideoCapture(0, cv2.CAP_MSMF)
            t_open = time.perf_counter()
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            cap.set(cv2.CAP_PROP_FPS, 30)
            t_config = time.perf_counter()
            open_ms = (t_open - t0) * 1000.0
            config_ms = (t_config - t_open) * 1000.0
        elif condition == "CONSTRUCTOR_PARAMS":
            params = [
                cv2.CAP_PROP_FRAME_WIDTH, 1280,
                cv2.CAP_PROP_FRAME_HEIGHT, 720,
                cv2.CAP_PROP_FPS, 30,
            ]
            cap = cv2.VideoCapture(0, cv2.CAP_MSMF, params)
            t_open = time.perf_counter()
            t_config = t_open
            open_ms = (t_open - t0) * 1000.0
            config_ms = 0.0
        else:
            raise ValueError(f"Unknown condition: {condition}")

        if not cap.isOpened():
            raise RuntimeError("VideoCapture failed to open camera device 0")

        # Read first frame
        t_before_first = time.perf_counter()
        ret, frame = cap.read()
        t_after_first = time.perf_counter()

        if not ret or frame is None:
            raise RuntimeError("VideoCapture failed to read first frame")

        first_frame_ms = (t_after_first - t_before_first) * 1000.0
        total_startup_ms = (t_after_first - t0) * 1000.0

        actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        actual_fps = float(cap.get(cv2.CAP_PROP_FPS))
        fourcc_int = int(cap.get(cv2.CAP_PROP_FOURCC))
        fourcc_str = "".join([chr((fourcc_int >> 8 * i) & 0xFF) for i in range(4)])

        print(f"  Startup: open={open_ms:.1f}ms, config={config_ms:.1f}ms, first_frame={first_frame_ms:.1f}ms (total={total_startup_ms:.1f}ms)")
        print(f"  Negotiated: {actual_w}x{actual_h} @ {actual_fps:.1f} FPS, FOURCC={fourcc_str}")

        # 20-second steady state capture
        print(f"  Measuring {duration_sec:.0f}s steady-state stream...", end="", flush=True)
        frame_timestamps: list[float] = []
        frame_intervals_ms: list[float] = []
        read_latencies_ms: list[float] = []
        total_frames = 0
        unique_frames = 0
        failed_reads = 0
        prev_sub = None

        t_stream_start = time.perf_counter()
        t_deadline = t_stream_start + duration_sec
        last_arrival = t_stream_start

        while True:
            t_read_start = time.perf_counter()
            if t_read_start >= t_deadline:
                break
            ret, frame = cap.read()
            t_read_end = time.perf_counter()

            if not ret or frame is None:
                failed_reads += 1
                continue

            total_frames += 1
            read_latencies_ms.append((t_read_end - t_read_start) * 1000.0)
            frame_intervals_ms.append((t_read_end - last_arrival) * 1000.0)
            frame_timestamps.append(t_read_end)
            last_arrival = t_read_end

            # Subsample 16x16 to check for frame uniqueness without heavy CPU
            sub = frame[::16, ::16].copy()
            if prev_sub is None or not np.array_equal(sub, prev_sub):
                unique_frames += 1
                prev_sub = sub

        t_stream_end = time.perf_counter()
        stream_duration = t_stream_end - t_stream_start
        effective_fps = total_frames / stream_duration if stream_duration > 0 else 0.0
        effective_unique_fps = unique_frames / stream_duration if stream_duration > 0 else 0.0

        print(f" Done! {total_frames} frames ({unique_frames} unique) in {stream_duration:.2f}s => {effective_fps:.2f} FPS")

        interval_stats = compute_stats(frame_intervals_ms)
        read_stats = compute_stats(read_latencies_ms)

        result = {
            "condition": condition,
            "trial": trial_num,
            "success": True,
            "startup": {
                "open_time_ms": round(open_ms, 2),
                "config_time_ms": round(config_ms, 2),
                "first_frame_time_ms": round(first_frame_ms, 2),
                "total_startup_ms": round(total_startup_ms, 2),
            },
            "negotiated": {
                "width": actual_w,
                "height": actual_h,
                "fps": actual_fps,
                "fourcc": fourcc_str,
            },
            "stream_20s": {
                "duration_sec": round(stream_duration, 3),
                "total_frames": total_frames,
                "unique_frames": unique_frames,
                "failed_reads": failed_reads,
                "effective_fps": round(effective_fps, 2),
                "effective_unique_fps": round(effective_unique_fps, 2),
                "frame_interval_ms": interval_stats,
                "read_latency_ms": read_stats,
            }
        }
    except Exception as e:
        print(f"  ERROR in trial: {e}")
        result = {
            "condition": condition,
            "trial": trial_num,
            "success": False,
            "error": str(e),
        }
    finally:
        if cap is not None:
            cap.release()
        time.sleep(1.0)  # Cooldown between trials

    return result


def main():
    print("==================================================================")
    print("CAMERA HARDWARE A/B BENCHMARK: SEQUENTIAL SET VS CONSTRUCTOR PARAMS")
    print("==================================================================")

    conditions_sequence = [
        "SEQUENTIAL_SET",
        "CONSTRUCTOR_PARAMS",
        "SEQUENTIAL_SET",
        "CONSTRUCTOR_PARAMS",
        "SEQUENTIAL_SET",
        "CONSTRUCTOR_PARAMS",
        "SEQUENTIAL_SET",
        "CONSTRUCTOR_PARAMS",
        "SEQUENTIAL_SET",
        "CONSTRUCTOR_PARAMS",
    ]

    trials_data: list[dict[str, Any]] = []

    for idx, cond in enumerate(conditions_sequence, start=1):
        trial_res = run_single_trial(cond, idx, duration_sec=20.0)
        trials_data.append(trial_res)

    def aggregate(cond_name: str) -> dict[str, Any]:
        c_data = [t for t in trials_data if t.get("condition") == cond_name and t.get("success")]
        if not c_data:
            return {}

        def extract_vals(path: list[str]) -> list[float]:
            res = []
            for item in c_data:
                cur = item
                for k in path:
                    cur = cur[k]
                res.append(float(cur))
            return res

        open_vals = extract_vals(["startup", "open_time_ms"])
        config_vals = extract_vals(["startup", "config_time_ms"])
        first_vals = extract_vals(["startup", "first_frame_time_ms"])
        total_vals = extract_vals(["startup", "total_startup_ms"])
        fps_vals = extract_vals(["stream_20s", "effective_fps"])
        uniq_fps_vals = extract_vals(["stream_20s", "effective_unique_fps"])
        interval_means = extract_vals(["stream_20s", "frame_interval_ms", "mean"])

        return {
            "successful_trials": len(c_data),
            "negotiated_resolution": f"{c_data[0]['negotiated']['width']}x{c_data[0]['negotiated']['height']}",
            "negotiated_fps": c_data[0]['negotiated']['fps'],
            "fourcc": c_data[0]['negotiated']['fourcc'],
            "open_time_ms": {
                "mean": round(float(np.mean(open_vals)), 2),
                "std": round(float(np.std(open_vals)), 2),
                "values": open_vals,
            },
            "config_time_ms": {
                "mean": round(float(np.mean(config_vals)), 2),
                "std": round(float(np.std(config_vals)), 2),
                "values": config_vals,
            },
            "first_frame_time_ms": {
                "mean": round(float(np.mean(first_vals)), 2),
                "std": round(float(np.std(first_vals)), 2),
                "values": first_vals,
            },
            "total_startup_ms": {
                "mean": round(float(np.mean(total_vals)), 2),
                "std": round(float(np.std(total_vals)), 2),
                "values": total_vals,
            },
            "effective_fps": {
                "mean": round(float(np.mean(fps_vals)), 2),
                "std": round(float(np.std(fps_vals)), 2),
                "values": fps_vals,
            },
            "effective_unique_fps": {
                "mean": round(float(np.mean(uniq_fps_vals)), 2),
                "std": round(float(np.std(uniq_fps_vals)), 2),
                "values": uniq_fps_vals,
            },
            "frame_interval_mean_ms": {
                "mean": round(float(np.mean(interval_means)), 2),
                "std": round(float(np.std(interval_means)), 2),
            }
        }

    seq_summary = aggregate("SEQUENTIAL_SET")
    params_summary = aggregate("CONSTRUCTOR_PARAMS")

    diff_open = round(params_summary["open_time_ms"]["mean"] - seq_summary["open_time_ms"]["mean"], 2)
    diff_config = round(params_summary["config_time_ms"]["mean"] - seq_summary["config_time_ms"]["mean"], 2)
    diff_first = round(params_summary["first_frame_time_ms"]["mean"] - seq_summary["first_frame_time_ms"]["mean"], 2)
    diff_total = round(params_summary["total_startup_ms"]["mean"] - seq_summary["total_startup_ms"]["mean"], 2)
    diff_fps = round(params_summary["effective_fps"]["mean"] - seq_summary["effective_fps"]["mean"], 2)

    final_report = {
        "metadata": {
            "test_type": "CAMERA_HARDWARE_CONSTRUCTOR_AB",
            "date": time.strftime("%Y-%m-%d %H:%M:%S"),
            "backend": "cv2.CAP_MSMF",
            "device_index": 0,
            "target_width": 1280,
            "target_height": 720,
            "target_fps": 30,
            "stream_test_duration_per_trial_sec": 20.0,
            "sequence": conditions_sequence,
        },
        "condition_sequential_set": seq_summary,
        "condition_constructor_params": params_summary,
        "delta_params_minus_sequential": {
            "open_time_ms": diff_open,
            "config_time_ms": diff_config,
            "first_frame_time_ms": diff_first,
            "total_startup_ms": diff_total,
            "effective_fps": diff_fps,
        },
        "trials": trials_data,
    }

    out_file = "camera_constructor_ab.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(final_report, f, indent=2)

    print("\n==================================================================")
    print("CAMERA HARDWARE A/B COMPLETE")
    print(f"Results written to {out_file}")
    print(f"Sequential Set Total Startup Mean: {seq_summary['total_startup_ms']['mean']:.2f} ms")
    print(f"Constructor Params Total Startup Mean: {params_summary['total_startup_ms']['mean']:.2f} ms")
    print(f"Delta (Constructor - Sequential): {diff_total:+.2f} ms")
    print(f"Sequential Set Effective FPS: {seq_summary['effective_fps']['mean']:.2f} FPS")
    print(f"Constructor Params Effective FPS: {params_summary['effective_fps']['mean']:.2f} FPS")
    print("==================================================================")


if __name__ == "__main__":
    main()
