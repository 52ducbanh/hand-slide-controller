import ctypes
from ctypes import wintypes
import threading
import time
import sys

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_longlong

user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL

user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL

user32.SetFocus.argtypes = [wintypes.HWND]
user32.SetFocus.restype = wintypes.HWND

events = []
lock = threading.Lock()

def wndproc(hwnd, msg, wparam, lparam):
    if msg == 0x0100: # WM_KEYDOWN
        t = time.perf_counter_ns()
        with lock:
            events.append(("DOWN", wparam, t))
        return 0
    elif msg == 0x0101: # WM_KEYUP
        t = time.perf_counter_ns()
        with lock:
            events.append(("UP", wparam, t))
        return 0
    elif msg == 0x0002: # WM_DESTROY
        user32.PostQuitMessage(0)
        return 0
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

cb = WNDPROC(wndproc)

class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ('cbSize', wintypes.UINT),
        ('style', wintypes.UINT),
        ('lpfnWndProc', WNDPROC),
        ('cbClsExtra', ctypes.c_int),
        ('cbWndExtra', ctypes.c_int),
        ('hInstance', wintypes.HINSTANCE),
        ('hIcon', wintypes.HICON),
        ('hCursor', wintypes.HANDLE),
        ('hbrBackground', wintypes.HBRUSH),
        ('lpszMenuName', wintypes.LPCWSTR),
        ('lpszClassName', wintypes.LPCWSTR),
        ('hIconSm', wintypes.HICON),
    ]

hInst = kernel32.GetModuleHandleW(None)
wc = WNDCLASSEXW()
wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
wc.style = 3
wc.lpfnWndProc = cb
wc.hInstance = hInst
wc.lpszClassName = 'DispatchReceiverTest'

user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
user32.RegisterClassExW.restype = wintypes.ATOM
atom = user32.RegisterClassExW(ctypes.byref(wc))

user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID
]
user32.CreateWindowExW.restype = wintypes.HWND

ready = threading.Event()
hwnd_holder = []

def thread_func():
    hwnd = user32.CreateWindowExW(
        0, 'DispatchReceiverTest', 'Dispatch Receiver Test Window',
        0x00CF0000, 200, 200, 300, 200, None, None, hInst, None
    )
    if not hwnd:
        ready.set()
        return
    hwnd_holder.append(hwnd)
    user32.ShowWindow(hwnd, 5) # SW_SHOW
    user32.SetForegroundWindow(hwnd)
    user32.SetFocus(hwnd)
    ready.set()

    msg = wintypes.MSG()
    while True:
        bRet = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
        if bRet <= 0:
            break
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))

t = threading.Thread(target=thread_func, daemon=True)
t.start()
ready.wait(timeout=3.0)

hwnd = hwnd_holder[0]
print("Created and focused hwnd:", hwnd)
time.sleep(0.5)

# Test SendInput
class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ('wVk', wintypes.WORD),
        ('wScan', wintypes.WORD),
        ('dwFlags', wintypes.DWORD),
        ('time', wintypes.DWORD),
        ('dwExtraInfo', ctypes.POINTER(ctypes.c_ulong))
    ]

class _INPUTunion(ctypes.Union):
    _fields_ = [('ki', KEYBDINPUT)]

class INPUT(ctypes.Structure):
    _anonymous_ = ('_input',)
    _fields_ = [
        ('type', wintypes.DWORD),
        ('_input', _INPUTunion)
    ]

user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT

def send_key(vk):
    inp_down = INPUT()
    inp_down.type = 1
    inp_down.ki.wVk = vk
    inp_down.ki.dwFlags = 0

    inp_up = INPUT()
    inp_up.type = 1
    inp_up.ki.wVk = vk
    inp_up.ki.dwFlags = 2 # KEYEVENTF_KEYUP

    user32.SendInput(1, ctypes.byref(inp_down), ctypes.sizeof(INPUT))
    user32.SendInput(1, ctypes.byref(inp_up), ctypes.sizeof(INPUT))

send_key(0x27) # VK_RIGHT
time.sleep(0.1)

import pyautogui
pyautogui.PAUSE = 0
pyautogui.FAILSAFE = False
pyautogui.press('right')
time.sleep(0.1)

with lock:
    print("Captured events count:", len(events))
    for e in events:
        print("  Event:", e[0], "VK:", hex(e[1]))

user32.PostMessageW(hwnd, 0x0002, 0, 0)
user32.UnregisterClassW('DispatchReceiverTest', hInst)
