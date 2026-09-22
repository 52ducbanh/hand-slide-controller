"""Camera Backend A/B Test Suite: MSMF vs DSHOW.
Runs 10 trials for each backend and computes statistics for:
- Startup time (opening capture & negotiating 1280x720)
- First frame latency
- Steady state frame read time (mean, p95, min, max)
- Effective frame arrival FPS
- Stability & failures
Outputs ab_camera_backend.json.
"""

from __future__ import annotations
import json
import time
import cv2
import numpy as np

def compute_stats(vals: list[float]) -> dict[str, float]:
    if not vals:
        return {"mean": 0.0, "p50": 0.0, "p95": 0.0, "p99": 0.0, "min": 0.0, "max": 0.0, "std": 0.0}
    arr = np.array(vals, dtype=np.float64)
    return {
        "mean": float(np.mean(arr)),
        "p50": float(np.percentile(arr, 50)),
        "p95": float(np.percentile(arr, 95)),
        "p99": float(np.percentile(arr, 99)),
        "min": float(np.min(arr)),
        "max": float(np.max(arr)),
        "std": float(np.std(arr)),
    }

def test_backend_trial(backend_name: str, backend_id: int, num_frames: int = 150) -> dict:
    t0 = time.perf_counter()
    cap = cv2.VideoCapture(0, backend_id)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    cap.set(cv2.CAP_PROP_FPS, 30)
    t_opened = time.perf_counter()
    startup_ms = (t_opened - t0) * 1000.0

    actual_w = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
    actual_h = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
    actual_fps = cap.get(cv2.CAP_PROP_FPS)

    # First frame
    t_first_start = time.perf_counter()
    ret, frame = cap.read()
    t_first_end = time.perf_counter()
    first_frame_ms = (t_first_end - t_first_start) * 1000.0

    if not ret or frame is None:
        cap.release()
        return {
            "success": False,
            "startup_ms": startup_ms,
            "first_frame_ms": first_frame_ms,
            "error": "Failed to read first frame"
        }

    read_times_ms: list[float] = []
    intervals_ms: list[float] = []
    failed_reads = 0

    last_arrival = time.perf_counter()
    for _ in range(num_frames):
        tr0 = time.perf_counter()
        ret, frame = cap.read()
        tr1 = time.perf_counter()
        if not ret:
            failed_reads += 1
            continue
        read_times_ms.append((tr1 - tr0) * 1000.0)
        intervals_ms.append((tr1 - last_arrival) * 1000.0)
        last_arrival = tr1

    cap.release()
    time.sleep(0.5) # Give driver cooldown between trials

    read_stats = compute_stats(read_times_ms)
    interval_stats = compute_stats(intervals_ms)
    effective_fps = 1000.0 / interval_stats["mean"] if interval_stats["mean"] > 0 else 0.0

    return {
        "success": True,
        "startup_ms": startup_ms,
        "first_frame_ms": first_frame_ms,
        "negotiated_width": actual_w,
        "negotiated_height": actual_h,
        "negotiated_fps": actual_fps,
        "failed_reads": failed_reads,
        "effective_fps": effective_fps,
        "read_stats": read_stats,
        "interval_stats": interval_stats,
    }

def run_camera_ab_test(trials: int = 5):
    print(f"=== Starting Camera Backend A/B Test ({trials} trials each) ===")
    results = {
        "MSMF": [],
        "DSHOW": [],
    }

    backends = [
        ("DSHOW", cv2.CAP_DSHOW),
        ("MSMF", cv2.CAP_MSMF),
    ]

    for trial in range(1, trials + 1):
        print(f"\n--- Trial {trial}/{trials} ---")
        for name, bid in backends:
            print(f"Testing {name}...")
            res = test_backend_trial(name, bid, num_frames=120)
            results[name].append(res)
            print(f"  {name}: startup={res['startup_ms']:.1f}ms, first_frame={res['first_frame_ms']:.1f}ms, "
                  f"read_mean={res['read_stats']['mean']:.2f}ms, read_p95={res['read_stats']['p95']:.2f}ms, "
                  f"effective_fps={res['effective_fps']:.2f}, failures={res['failed_reads']}")

    # Aggregate summaries
    summary = {}
    for name in ["MSMF", "DSHOW"]:
        runs = [r for r in results[name] if r["success"]]
        startups = [r["startup_ms"] for r in runs]
        first_frames = [r["first_frame_ms"] for r in runs]
        fps_list = [r["effective_fps"] for r in runs]
        read_means = [r["read_stats"]["mean"] for r in runs]
        read_p95s = [r["read_stats"]["p95"] for r in runs]
        total_failures = sum(r["failed_reads"] for r in runs)

        summary[name] = {
            "trials": len(runs),
            "startup_ms": compute_stats(startups),
            "first_frame_ms": compute_stats(first_frames),
            "effective_fps": compute_stats(fps_list),
            "read_mean_ms": compute_stats(read_means),
            "read_p95_ms": compute_stats(read_p95s),
            "total_failures": total_failures,
            "stability_rate": (1.0 - total_failures / (len(runs) * 120)) * 100.0,
        }

    output = {
        "summary": summary,
        "raw_trials": results,
    }

    with open("ab_camera_backend.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
    print("\nSaved camera backend results to ab_camera_backend.json")
    return output

if __name__ == "__main__":
    run_camera_ab_test(trials=5)
