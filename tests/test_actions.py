"""Unit tests for ActionDispatcher, KeySender, and AudioPlayer abstractions."""

from __future__ import annotations
import pytest

from hand_controller.models import SlideAction
from hand_controller.actions import (
    ActionDispatcher,
    KeySender,
    AudioPlayer,
    NullAudioPlayer,
    VK_LEFT,
    VK_RIGHT,
    VK_B,
    ACTION_DISPLAY_TEXT,
    ACTION_TO_VK,
)


class MockKeySender:
    def __init__(self) -> None:
        self.sent_vks: list[int] = []

    def send_key(self, vk: int) -> bool:
        self.sent_vks.append(vk)
        return True


class MockAudioPlayer:
    def __init__(self) -> None:
        self.play_count: int = 0

    def play_action_feedback(self) -> None:
        self.play_count += 1


def test_action_dispatcher_none_action():
    sender = MockKeySender()
    audio = MockAudioPlayer()
    dispatcher = ActionDispatcher(cooldown=0.5, audio_feedback=True, key_sender=sender, audio_player=audio)

    assert dispatcher.dispatch(SlideAction.NONE, now=1.0) is False
    assert len(sender.sent_vks) == 0
    assert audio.play_count == 0
    assert dispatcher.last_action_time == 0.0
    assert dispatcher.last_action_text == ""


def test_action_dispatcher_dispatches_previous_next_blackout():
    sender = MockKeySender()
    audio = MockAudioPlayer()
    dispatcher = ActionDispatcher(cooldown=0.2, audio_feedback=True, key_sender=sender, audio_player=audio)

    # PREVIOUS -> VK_LEFT
    res_prev = dispatcher.dispatch(SlideAction.PREVIOUS, now=1.0)
    assert res_prev is True
    assert sender.sent_vks == [VK_LEFT]
    assert audio.play_count == 1
    assert dispatcher.last_action_time == 1.0
    assert dispatcher.last_action_text == ACTION_DISPLAY_TEXT[SlideAction.PREVIOUS]

    # NEXT -> VK_RIGHT
    res_next = dispatcher.dispatch(SlideAction.NEXT, now=1.3)
    assert res_next is True
    assert sender.sent_vks == [VK_LEFT, VK_RIGHT]
    assert audio.play_count == 2
    assert dispatcher.last_action_time == 1.3
    assert dispatcher.last_action_text == ACTION_DISPLAY_TEXT[SlideAction.NEXT]

    # BLACKOUT -> VK_B
    res_blk = dispatcher.dispatch(SlideAction.BLACKOUT, now=1.6)
    assert res_blk is True
    assert sender.sent_vks == [VK_LEFT, VK_RIGHT, VK_B]
    assert audio.play_count == 3
    assert dispatcher.last_action_time == 1.6
    assert dispatcher.last_action_text == ACTION_DISPLAY_TEXT[SlideAction.BLACKOUT]


def test_action_dispatcher_cooldown_rate_limiting():
    sender = MockKeySender()
    dispatcher = ActionDispatcher(cooldown=0.5, key_sender=sender)

    assert dispatcher.dispatch(SlideAction.NEXT, now=10.0) is True
    assert sender.sent_vks == [VK_RIGHT]

    # Within cooldown: rejected
    assert dispatcher.dispatch(SlideAction.NEXT, now=10.2) is False
    assert dispatcher.dispatch(SlideAction.PREVIOUS, now=10.49) is False
    assert sender.sent_vks == [VK_RIGHT]

    # After cooldown: accepted
    assert dispatcher.dispatch(SlideAction.PREVIOUS, now=10.51) is True
    assert sender.sent_vks == [VK_RIGHT, VK_LEFT]


def test_action_dispatcher_audio_feedback_disabled():
    sender = MockKeySender()
    audio = MockAudioPlayer()
    dispatcher = ActionDispatcher(cooldown=0.2, audio_feedback=False, key_sender=sender, audio_player=audio)

    assert dispatcher.dispatch(SlideAction.NEXT, now=1.0) is True
    assert audio.play_count == 0


def test_null_audio_player():
    player = NullAudioPlayer()
    # Ensure method exists and executes without error
    player.play_action_feedback()
