from __future__ import annotations
import ctypes
from ctypes import wintypes
import sys
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

KEYEVENTF_KEYUP = 0x0002
INPUT_KEYBOARD = 1


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _INPUTunion(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT)]


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
    inp_down = INPUT()
    inp_down.type = INPUT_KEYBOARD
    inp_down.ki.wVk = vk
    inp_down.ki.dwFlags = 0

    inp_up = INPUT()
    inp_up.type = INPUT_KEYBOARD
    inp_up.ki.wVk = vk
    inp_up.ki.dwFlags = KEYEVENTF_KEYUP

    user32.SendInput(1, ctypes.byref(inp_down), ctypes.sizeof(INPUT))
    user32.SendInput(1, ctypes.byref(inp_up), ctypes.sizeof(INPUT))


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