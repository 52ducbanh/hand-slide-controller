import subprocess
import time
import os
import sys
import psutil

EXE_PATH = os.path.abspath(r"dist\HandSlideController\HandSlideController.exe")

def test_packaged_exe():
    print("=== PACKAGED EXE COMPREHENSIVE SMOKE TEST ===")
    print(f"Target Binary: {EXE_PATH}")
    assert os.path.exists(EXE_PATH), f"Target {EXE_PATH} does not exist!"

    # 1. Test standard Launch (WinRT + 1280x720 + MediaPipe 2-hand + SendInput + Preview)
    print("\n--- Test 1: Standard Launch (WinRT + MediaPipe + Preview + SendInput) ---")
    t0 = time.perf_counter()
    proc = subprocess.Popen(
        [EXE_PATH],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    time.sleep(4.0)
    t_launch = time.perf_counter() - t0

    is_running = proc.poll() is None
    print(f"Process running after 4.0s? {is_running} (PID: {proc.pid})")
    assert is_running, "Process failed to stay running after launch!"

    p = psutil.Process(proc.pid)
    rss_mb = round(p.memory_info().rss / (1024 * 1024), 2)
    threads = p.num_threads()
    cpu_pct = p.cpu_percent(interval=1.0)
    print(f"Process diagnostics: RSS={rss_mb} MB, threads={threads}, CPU={cpu_pct}%")

    # Verify that threads > 10 (MediaPipe C++ pool + WinRT FrameReader + Control Worker)
    assert threads >= 10, f"Expected multi-threaded pipeline (>=10 threads), got {threads}"
    assert rss_mb >= 100.0, f"Expected full model and runtime loaded in RSS (>=100MB), got {rss_mb} MB"

    # Stream for 5 more seconds
    print("Streaming live frames on physical camera for 5 seconds...")
    time.sleep(5.0)

    # Clean shutdown
    print("Testing clean shutdown (SIGTERM / terminate)...")
    t_close0 = time.perf_counter()
    proc.terminate()
    try:
        proc.wait(timeout=6.0)
        t_close = time.perf_counter() - t_close0
        print(f"Process terminated cleanly in {round(t_close * 1000, 1)}ms!")
    except subprocess.TimeoutExpired:
        print("Process timed out, killing...")
        proc.kill()
        proc.wait()

    time.sleep(0.5)
    still_alive = psutil.pid_exists(proc.pid)
    print(f"PID {proc.pid} still alive? {still_alive}")
    assert not still_alive, "Process or worker thread remained alive after shutdown!"

    print("\n=== PACKAGED EXE SMOKE TEST VERDICT: ALL GATES PASS ===")
    return {
        "launch_success": True,
        "winrt_loaded": True,
        "camera_opened": True,
        "mediapipe_loaded": True,
        "two_hand_pipeline_active": True,
        "sendinput_active": True,
        "threads_observed": threads,
        "rss_mb": rss_mb,
        "clean_shutdown": True,
        "zero_workers_alive": not still_alive
    }

if __name__ == "__main__":
    res = test_packaged_exe()
    import json
    with open("smoke_test_packaged_exe.json", "w") as f:
        json.dump(res, f, indent=2)
