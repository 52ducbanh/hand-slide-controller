import platform
import sys
import hashlib
import json
import subprocess
import os

import cv2
import mediapipe
import numpy

def get_git_info():
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    status = subprocess.check_output(["git", "status", "--short"], text=True).strip()
    return commit, status

def get_wmi_info():
    import ctypes
    # Query CPU via registry or platform
    cpu = platform.processor()
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
        cpu_name, _ = winreg.QueryValueEx(key, "ProcessorNameString")
        winreg.CloseKey(key)
    except Exception:
        cpu_name = cpu

    # Query RAM via GlobalMemoryStatusEx
    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]
    stat = MEMORYSTATUSEX()
    stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
    ram_gb = round(stat.ullTotalPhys / (1024**3), 2)

    return cpu_name.strip(), ram_gb

def get_pip_version(pkg_name):
    try:
        r = subprocess.run([sys.executable, "-m", "pip", "show", pkg_name], capture_output=True, text=True)
        for line in r.stdout.splitlines():
            if line.startswith("Version:"):
                return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return "UNKNOWN"

commit, git_status = get_git_info()
cpu_name, ram_gb = get_wmi_info()

# Model hash
model_bytes = open("hand_landmarker.task", "rb").read()
model_sha256 = hashlib.sha256(model_bytes).hexdigest()

baseline = {
    "schema_version": "1.0",
    "timestamp_iso": "2026-09-22T11:27:00+07:00",
    "baseline_commit": commit,
    "git_status": git_status,
    "environment": {
        "os_name": "Windows",
        "windows_version": platform.version(),
        "windows_platform": platform.platform(),
        "cpu_model": cpu_name,
        "ram_total_gb": ram_gb,
        "python_version": sys.version.split()[0],
        "python_full": sys.version,
        "opencv_version": cv2.__version__,
        "mediapipe_version": mediapipe.__version__,
        "numpy_version": numpy.__version__,
        "winrt_runtime_version": get_pip_version("winrt-runtime"),
        "pyautogui_version": get_pip_version("PyAutoGUI"),
        "pyinstaller_version": get_pip_version("pyinstaller"),
        "model_file": "hand_landmarker.task",
        "model_sha256": model_sha256,
        "model_size_bytes": len(model_bytes)
    }
}

print(json.dumps(baseline, indent=2))
with open("FINAL_PRODUCTION_BASELINE.json", "w", encoding="utf-8") as f:
    json.dump(baseline, f, indent=2)
print("Saved FINAL_PRODUCTION_BASELINE.json")
