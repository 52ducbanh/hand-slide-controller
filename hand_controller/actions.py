from __future__ import annotations
import ctypes
from ctypes import wintypes
import sys
import time
from .models import SlideAction, ACTION_TO_KEY


ACTION_DISPLAY_TEXT: dict[SlideAction, str] = {
    SlideAction.PREVIOUS: "SCISSORS -> LEFT ARROW",
    SlideAction.NEXT: "LIKE -> RIGHT ARROW",
    SlideAction.BLACKOUT: "OPEN PALM -> B (BLACKOUT)",
}

user32 = ctypes.WinDLL("user32", use_last_error=True)

VK_LEFT = 0x25
VK_RIGHT = 0x27
VK_B = 0x42

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
INPUT_KEYBOARD = 1

EXTENDED_VKS = {VK_LEFT, VK_RIGHT}


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTunion(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _anonymous_ = ("_input",)
    _fields_ = [
        ("type", wintypes.DWORD),
        ("_input", _INPUTunion),
    ]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT

ACTION_TO_VK: dict[SlideAction, int] = {
    SlideAction.PREVIOUS: VK_LEFT,
    SlideAction.NEXT: VK_RIGHT,
    SlideAction.BLACKOUT: VK_B,
}


def _send_vk(vk: int) -> None:
    scan = user32.MapVirtualKeyW(vk, 0)
    flags_down = KEYEVENTF_EXTENDEDKEY if vk in EXTENDED_VKS else 0
    flags_up = flags_down | KEYEVENTF_KEYUP

    # 1. Primary path: Native 64-bit SendInput (cbSize = 40 bytes)
    inp_down = INPUT()
    inp_down.type = INPUT_KEYBOARD
    inp_down.ki.wVk = vk
    inp_down.ki.wScan = scan
    inp_down.ki.dwFlags = flags_down

    ret_down = user32.SendInput(1, ctypes.byref(inp_down), ctypes.sizeof(INPUT))

    # Hold key for 15ms so the target app's message pump registers the WM_KEYDOWN
    time.sleep(0.015)

    inp_up = INPUT()
    inp_up.type = INPUT_KEYBOARD
    inp_up.ki.wVk = vk
    inp_up.ki.wScan = scan
    inp_up.ki.dwFlags = flags_up

    ret_up = user32.SendInput(1, ctypes.byref(inp_up), ctypes.sizeof(INPUT))

    # 2. Bulletproof Fallback: keybd_event if SendInput fails or is blocked
    if ret_down == 0 or ret_up == 0:
        user32.keybd_event(vk, scan, flags_down, 0)
        time.sleep(0.015)
        user32.keybd_event(vk, scan, flags_up, 0)


class ActionDispatcher:
    def __init__(self, cooldown: float, audio_feedback: bool = False) -> None:
        self._cooldown = cooldown
        self._audio_feedback = audio_feedback
        self._last_action_time: float = 0.0
        self._last_action_text: str = ""

    def dispatch(self, action: SlideAction, now: float) -> bool:
        if action == SlideAction.NONE:
            return False
        if now - self._last_action_time < self._cooldown:
            return False
        vk = ACTION_TO_VK.get(action)
        if vk is None:
            return False
        _send_vk(vk)
        self._last_action_time = now
        self._last_action_text = self._make_text(action)
        if sys.stdout is not None:
            try:
                print(self._last_action_text)
            except Exception:
                pass
        if self._audio_feedback:
            self._play_feedback_sound()
        return True

    @property
    def last_action_time(self) -> float:
        return self._last_action_time

    @property
    def last_action_text(self) -> str:
        return self._last_action_text

    @staticmethod
    def _make_text(action: SlideAction) -> str:
        return ACTION_DISPLAY_TEXT.get(action, "")

    @staticmethod
    def _play_feedback_sound() -> None:
        try:
            import winsound
            winsound.PlaySound("NavigationStart", winsound.SND_ALIAS | winsound.SND_ASYNC)
        except Exception:
            try:
                import winsound
                winsound.MessageBeep(-1)
            except Exception:
                pass