from __future__ import annotations
import sys
import pyautogui
from .models import SlideAction, ACTION_TO_KEY


pyautogui.PAUSE = 0


ACTION_DISPLAY_TEXT: dict[SlideAction, str] = {
    SlideAction.PREVIOUS: "SCISSORS -> LEFT ARROW",
    SlideAction.NEXT: "LIKE -> RIGHT ARROW",
    SlideAction.BLACKOUT: "OPEN PALM -> B (BLACKOUT)",
}


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
        key = ACTION_TO_KEY.get(action)
        if key is None:
            return False
        pyautogui.press(key)
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