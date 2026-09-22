import time
import json
import psutil
from hand_controller.config import CameraConfig
from hand_controller.camera import create_camera_source

def test_10x_reopen():
    print("=== FINAL 10x CAMERA REOPEN TEST ===")
    results = []
    cfg = CameraConfig(backend="AUTO")

    for i in range(10):
        t0 = time.perf_counter()
        cam = create_camera_source(cfg)
        t_open = time.perf_counter() - t0

        # Stream 5 frames
        frames_ok = 0
        for _ in range(5):
            success, frame = cam.read_latest(timeout_sec=1.5)
            if success and frame is not None:
                frames_ok += 1
            time.sleep(0.033)

        t_close0 = time.perf_counter()
        cam.close()
        t_close = time.perf_counter() - t_close0

        rss_mb = round(psutil.Process().memory_info().rss / (1024 * 1024), 2)
        print(f"Iteration {i+1}/10: open={round(t_open*1000, 1)}ms, stream={frames_ok}/5, close={round(t_close*1000, 1)}ms, RSS={rss_mb}MB")

        results.append({
            "iteration": i + 1,
            "open_sec": round(t_open, 4),
            "stream_ok": frames_ok == 5,
            "close_sec": round(t_close, 4),
            "rss_mb": rss_mb
        })
        time.sleep(0.1)

    all_stream_ok = all(r["stream_ok"] for r in results)
    avg_open = round(sum(r["open_sec"] for r in results) / 10 * 1000, 1)
    avg_close = round(sum(r["close_sec"] for r in results) / 10 * 1000, 1)

    summary = {
        "total_trials": 10,
        "successful_opens": 10,
        "successful_streams": sum(1 for r in results if r["stream_ok"]),
        "successful_closes": 10,
        "all_pass": all_stream_ok,
        "avg_open_ms": avg_open,
        "avg_close_ms": avg_close,
        "trials": results
    }

    print(f"\nSummary: 10/10 Open, {summary['successful_streams']}/10 Stream, 10/10 Close. Avg Open: {avg_open}ms, Avg Close: {avg_close}ms")
    with open("camera_reopen_10x_final.json", "w") as f:
        json.dump(summary, f, indent=2)
    return all_stream_ok

if __name__ == "__main__":
    success = test_10x_reopen()
    print("10x Reopen Verdict:", "PASS" if success else "FAIL")
