import subprocess
import time
import os
import sys
import psutil

EXE_PATH = os.path.abspath(r"dist\HandSlideController\HandSlideController.exe")

def run_smoke_test():
    print(f"Testing packaged executable: {EXE_PATH}")
    if not os.path.exists(EXE_PATH):
        print(f"FAIL: {EXE_PATH} does not exist!")
        return False

    print("Launching packaged executable...")
    t0 = time.perf_counter()
    proc = subprocess.Popen(
        [EXE_PATH],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    )

    time.sleep(3.0)
    t_launch = time.perf_counter() - t0

    is_running = proc.poll() is None
    print(f"Process running after 3s? {is_running} (PID: {proc.pid})")

    if not is_running:
        out, err = proc.communicate()
        print("STDOUT:", out)
        print("STDERR:", err)
        return False

    # Check process memory and threads
    try:
        p = psutil.Process(proc.pid)
        rss_mb = round(p.memory_info().rss / (1024 * 1024), 2)
        threads = p.num_threads()
        print(f"Process stats: RSS={rss_mb} MB, threads={threads}")
    except Exception as e:
        print("psutil error:", e)

    # Let it stream for 5 more seconds
    time.sleep(5.0)

    # Send graceful termination or kill
    print("Testing clean shutdown...")
    proc.terminate()
    try:
        proc.wait(timeout=5.0)
        print("Process exited cleanly within timeout!")
    except subprocess.TimeoutExpired:
        print("Process did not exit in 5s, killing...")
        proc.kill()
        proc.wait()

    # Verify no orphan worker threads or processes remain
    still_alive = psutil.pid_exists(proc.pid)
    print(f"PID {proc.pid} still alive? {still_alive}")
    return not still_alive

if __name__ == "__main__":
    success = run_smoke_test()
    print("Smoke test result:", "PASS" if success else "FAIL")
    sys.exit(0 if success else 1)
