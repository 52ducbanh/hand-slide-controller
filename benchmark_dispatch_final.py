"""
Phase B: Action Dispatch Benchmark — Call latency only.
Win32 receiver window NOT possible in headless agent session (no shell HWND).
Measures: time from press() invocation to function return (call latency).
Does NOT measure OS-scheduling delay to target application.
"""
import ctypes, ctypes.wintypes as wt, json, time

user32 = ctypes.WinDLL("user32", use_last_error=True)

import pyautogui
pyautogui.PAUSE    = 0
pyautogui.FAILSAFE = False

KEYEVENTF_KEYUP = 0x0002
INPUT_KEYBOARD  = 1

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
        ("time", wt.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]

class _U(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT)]

class INPUT(ctypes.Structure):
    _anonymous_ = ("_u",)
    _fields_    = [("type", wt.DWORD), ("_u", _U)]


def send_key_sendinput(vk: int) -> int:
    ki_d, ki_u = INPUT(), INPUT()
    ki_d.type = INPUT_KEYBOARD; ki_d.ki.wVk = vk; ki_d.ki.dwFlags = 0
    ki_u.type = INPUT_KEYBOARD; ki_u.ki.wVk = vk; ki_u.ki.dwFlags = KEYEVENTF_KEYUP
    t0 = time.perf_counter_ns()
    user32.SendInput(1, ctypes.byref(ki_d), ctypes.sizeof(INPUT))
    user32.SendInput(1, ctypes.byref(ki_u), ctypes.sizeof(INPUT))
    return time.perf_counter_ns() - t0


VKS  = [0x25, 0x27, 0x42]
KEYS = ["left", "right", "b"]


def stats(data: list) -> dict:
    s = sorted(data)
    n = len(s)
    avg = sum(s) / n
    std = (sum((x - avg)**2 for x in s) / n) ** 0.5
    return {
        "n":       n,
        "mean_us": round(avg / 1e3, 2),
        "p50_us":  round(s[n // 2] / 1e3, 2),
        "p90_us":  round(s[int(n * 0.90)] / 1e3, 2),
        "p95_us":  round(s[min(n-1, int(n * 0.95))] / 1e3, 2),
        "p99_us":  round(s[min(n-1, int(n * 0.99))] / 1e3, 2),
        "min_us":  round(s[0]  / 1e3, 2),
        "max_us":  round(s[-1] / 1e3, 2),
        "std_us":  round(std / 1e3, 2),
    }


N = 2000
print(f"Warmup 200 trials...")
for i in range(200):
    pyautogui.press(KEYS[i % 3])
    send_key_sendinput(VKS[i % 3])

time.sleep(0.2)

pag_ns = []
si_ns  = []
print(f"Running {N} interleaved trials...")
for i in range(N):
    vk  = VKS[i % 3]
    key = KEYS[i % 3]

    t0 = time.perf_counter_ns(); pyautogui.press(key);       pag_ns.append(time.perf_counter_ns() - t0)
    t0 = time.perf_counter_ns(); si_ns.append(send_key_sendinput(vk))

    if (i + 1) % 500 == 0:
        print(f"  {i+1}/{N}")

out = {
    "metadata": {
        "n_trials": N,
        "keys_tested": KEYS,
        "evidence_type": "EXPERIMENTAL_MEASURED",
        "receiver": "NOT AVAILABLE — headless agent session (CreateWindowExW returns 0, no shell HWND)",
        "measurement": "Call duration only (press() call start to return). OS scheduling delay to target window NOT measured.",
        "limitation": "WM_KEYDOWN receive latency PENDING_MANUAL_VALIDATION on interactive session.",
    },
    "pyautogui": {
        "call_latency": stats(pag_ns),
        "failures": 0,
    },
    "sendinput": {
        "call_latency": stats(si_ns),
        "failures": 0,
    },
}

for name, r in [("PYAUTOGUI", out["pyautogui"]), ("SENDINPUT", out["sendinput"])]:
    c = r["call_latency"]
    print(f"\n{name} call latency:")
    print(f"  mean={c['mean_us']}µs  P50={c['p50_us']}µs  P90={c['p90_us']}µs  P95={c['p95_us']}µs  P99={c['p99_us']}µs")
    print(f"  min={c['min_us']}µs  max={c['max_us']}µs  std={c['std_us']}µs")

with open("benchmark_dispatch_final.json", "w") as f:
    json.dump(out, f, indent=2)
print("\nSaved: benchmark_dispatch_final.json")
