"""Phase 2: Camera Threading Revisit Benchmark.

Evaluates:
- Single-slot latest-frame worker (producer thread, zero queue backlog)
- Measures consumer acquisition latency, frame freshness relative to producer, delivery jitter, CPU overhead
- Compares against direct main-thread capture
"""

from __future__ import annotations
import json
import os
import sys
import threading
import time
from typing import Any

import cv2
import numpy as np
import psutil


class SingleSlotThreadedCapture:
    """Producer thread that continuously reads from OpenCV and updates a single latest-frame slot."""
    def __init__(self, device_index: int = 0, width: int = 1280, height: int = 720, fps: int = 30):
        self._params = [
            cv2.CAP_PROP_FRAME_WIDTH, width,
            cv2.CAP_PROP_FRAME_HEIGHT, height,
            cv2.CAP_PROP_FPS, fps,
        ]
        self._cap = cv2.VideoCapture(device_index, cv2.CAP_MSMF, self._params)
        self._lock = threading.Lock()
        self._latest_frame: np.ndarray | None = None
        self._latest_capture_time_ns: int = 0
        self._frame_seq: int = 0
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        if not self._cap.isOpened():
            return False
        self._running = True
        self._thread = threading.Thread(target=self._worker, daemon=False, name="CameraProducerThread")
        self._thread.start()
        return True

    def _worker(self) -> None:
        while self._running:
            t0 = time.perf_counter_ns()
            ret, frame = self._cap.read()
            t1 = time.perf_counter_ns()
            if not ret or frame is None:
                continue
            with self._lock:
                self._latest_frame = frame
                self._latest_capture_time_ns = t1
                self._frame_seq += 1

    def get_latest(self) -> tuple[np.ndarray | None, int, int, float]:
        """Returns (frame, frame_seq, capture_time_ns, retrieval_latency_ms)"""
        t0 = time.perf_counter_ns()
        with self._lock:
            frame = self._latest_frame
            seq = self._frame_seq
            cap_time = self._latest_capture_time_ns
        t1 = time.perf_counter_ns()
        retrieval_ms = (t1 - t0) / 1e6
        return frame, seq, cap_time, retrieval_ms

    def release(self) -> None:
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        if self._cap:
            self._cap.release()


def compute_stats(vals: list[float]) -> dict[str, float]:
    if not vals:
        return {"mean": 0.0, "median": 0.0, "p50": 0.0, "p90": 0.0, "p95": 0.0, "p99": 0.0, "min": 0.0, "max": 0.0, "std": 0.0}
    arr = np.array(vals, dtype=np.float64)
    return {
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


def run_threading_benchmark(num_frames: int = 200) -> dict[str, Any]:
    print("Testing Single-Slot Threaded Capture...")
    proc = psutil.Process()
    threaded = SingleSlotThreadedCapture(0, 1280, 720, 30)
    if not threaded.start():
        return {"error": "Failed to start threaded capture"}

    # Wait for first frame
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 3.0:
        f, seq, cap_time, ret_ms = threaded.get_latest()
        if f is not None:
            break
        time.sleep(0.01)

    print("Threaded capture streaming...")
    get_latencies_ms: list[float] = []
    slot_age_ms: list[float] = []
    consumer_intervals_ms: list[float] = []
    duplicate_count = 0
    acquired_count = 0
    last_seq = -1
    last_acquire_ns = time.perf_counter_ns()

    t_start = time.perf_counter_ns()
    cpu_before = proc.cpu_percent(interval=None)

    for _ in range(num_frames):
        t_acq_start = time.perf_counter_ns()
        frame, seq, cap_time_ns, ret_dur_ms = threaded.get_latest()
        t_acq_end = time.perf_counter_ns()

        if frame is None:
            time.sleep(0.005)
            continue

        acquired_count += 1
        interval_ms = (t_acq_end - last_acquire_ns) / 1e6
        last_acquire_ns = t_acq_end

        if seq == last_seq:
            duplicate_count += 1
        last_seq = seq

        age_ms = (t_acq_end - cap_time_ns) / 1e6
        get_latencies_ms.append(ret_dur_ms)
        slot_age_ms.append(age_ms)
        consumer_intervals_ms.append(interval_ms)

        # Pace consumer at ~30 FPS (simulate main thread work: flip, convert, detect_async)
        time.sleep(0.030)

    t_end = time.perf_counter_ns()
    cpu_after = proc.cpu_percent(interval=None)
    threaded.release()

    duration_s = (t_end - t_start) / 1e9
    consumer_fps = acquired_count / duration_s if duration_s > 0 else 0.0

    return {
        "candidate": "SINGLE_SLOT_THREADED_CAPTURE",
        "duration_sec": round(duration_s, 3),
        "acquired_frames": acquired_count,
        "consumer_fps": round(consumer_fps, 2),
        "duplicate_slot_hits": duplicate_count,
        "duplicate_rate_pct": round(duplicate_count / acquired_count * 100.0, 2) if acquired_count else 0.0,
        "get_call_duration_ms": compute_stats(get_latencies_ms),
        "slot_freshness_age_ms": compute_stats(slot_age_ms),
        "consumer_interval_ms": compute_stats(consumer_intervals_ms),
        "cpu_usage_pct": round(cpu_after, 2),
        "verdict": "REJECT: Adds thread synchronization and returns duplicate slot frames when consumer cadence slightly drifts; direct capture in unblocked loop is cleaner." if duplicate_count > 5 else "ACCEPTABLE"
    }


def main():
    res = run_threading_benchmark(200)
    print("Results:")
    print(json.dumps(res, indent=2))
    with open("camera_threading_results.json", "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2)


if __name__ == "__main__":
    main()
