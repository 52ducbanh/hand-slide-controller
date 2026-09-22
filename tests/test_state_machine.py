from unittest.mock import MagicMock
from hand_controller.config import TrackingConfig
from hand_controller.state_machine import GestureStateMachine
from hand_controller.models import (
    HandTrack,
    HandDetection,
    GestureResult,
    Gesture,
    SlideAction,
    GesturePhase,
    GESTURE_TO_ACTION,
)


def make_sm() -> GestureStateMachine:
    return GestureStateMachine(TrackingConfig())


def make_landmark(x: float = 0.5, y: float = 0.5, z: float = 0.0):
    lm = MagicMock()
    lm.x = x
    lm.y = y
    lm.z = z
    return lm


def make_detection(gesture: Gesture, wrist_x: float = 0.5, wrist_y: float = 0.5) -> HandDetection:
    gr = GestureResult(gesture=gesture)
    landmarks = [make_landmark(wrist_x, wrist_y)] * 21
    return HandDetection(landmarks=landmarks, wrist_x=wrist_x, wrist_y=wrist_y, gesture_result=gr)



def test_idle_to_stabilizing_on_gesture():
    sm = make_sm()
    track = HandTrack(track_id=0)
    action = sm.update(track, make_detection(Gesture.SCISSORS), now=0.0)
    assert track.phase == GesturePhase.STABILIZING
    assert track.candidate == Gesture.SCISSORS
    assert action == SlideAction.NONE


def test_stabilizing_stays_until_stable_time():
    sm = make_sm()
    track = HandTrack(track_id=0)
    sm.update(track, make_detection(Gesture.SCISSORS), now=0.0)
    sm.update(track, make_detection(Gesture.SCISSORS), now=0.05)
    assert track.phase == GesturePhase.STABILIZING


def test_stabilizing_to_armed_after_stable_time():
    sm = make_sm()
    track = HandTrack(track_id=0)
    sm.update(track, make_detection(Gesture.SCISSORS), now=0.0)
    action = sm.update(track, make_detection(Gesture.SCISSORS), now=0.09)
    assert track.phase == GesturePhase.ARMED
    assert track.candidate == Gesture.SCISSORS
    assert action == SlideAction.PREVIOUS


def test_gesture_change_resets_candidate():
    sm = make_sm()
    track = HandTrack(track_id=0)
    sm.update(track, make_detection(Gesture.SCISSORS), now=0.0)
    sm.update(track, make_detection(Gesture.LIKE), now=0.05)
    assert track.candidate == Gesture.LIKE
    assert track.phase == GesturePhase.STABILIZING


def test_none_gesture_resets_to_idle():
    sm = make_sm()
    track = HandTrack(track_id=0)
    sm.update(track, make_detection(Gesture.SCISSORS), now=0.0)
    action = sm.update(track, make_detection(Gesture.NONE), now=0.05)
    assert track.phase == GesturePhase.IDLE
    assert track.candidate == Gesture.NONE
    assert action == SlideAction.NONE


def test_latched_stays_latched_while_gesture_active():
    sm = make_sm()
    track = HandTrack(track_id=0)
    track.phase = GesturePhase.LATCHED
    action = sm.update(track, make_detection(Gesture.SCISSORS), now=1.0)
    assert track.phase == GesturePhase.LATCHED
    assert action == SlideAction.NONE


def test_latched_debounce_none_within_rearm_time():
    sm = make_sm()
    track = HandTrack(track_id=0)
    track.phase = GesturePhase.LATCHED
    sm.update(track, make_detection(Gesture.NONE), now=1.0)
    assert track.phase == GesturePhase.LATCHED
    sm.update(track, make_detection(Gesture.SCISSORS), now=1.05)
    assert track.phase == GesturePhase.LATCHED
    assert track.none_since == 0.0


def test_latched_rearms_after_sustained_none():
    sm = make_sm()
    track = HandTrack(track_id=0)
    track.phase = GesturePhase.LATCHED
    sm.update(track, make_detection(Gesture.NONE), now=1.0)
    assert track.phase == GesturePhase.LATCHED
    sm.update(track, make_detection(Gesture.NONE), now=1.16)
    assert track.phase == GesturePhase.IDLE
    assert track.candidate == Gesture.NONE
    assert track.none_since == 0.0


def test_moving_fast_blocks_stabilizing_to_armed():
    sm = make_sm()
    track = HandTrack(track_id=0)
    sm.update(track, make_detection(Gesture.SCISSORS), now=0.0)
    track.wrist_velocity = 2.5
    action = sm.update(track, make_detection(Gesture.SCISSORS), now=0.15)
    assert track.phase == GesturePhase.STABILIZING
    assert action == SlideAction.NONE


def test_moving_fast_blocks_armed_trigger():
    sm = make_sm()
    track = HandTrack(track_id=0)
    track.phase = GesturePhase.ARMED
    track.candidate = Gesture.LIKE
    track.wrist_velocity = 3.0
    action = sm.update(track, make_detection(Gesture.LIKE), now=1.0)
    assert action == SlideAction.NONE


def test_slowing_down_triggers_action():
    sm = make_sm()
    track = HandTrack(track_id=0)
    track.phase = GesturePhase.ARMED
    track.candidate = Gesture.LIKE
    track.wrist_velocity = 0.4
    action = sm.update(track, make_detection(Gesture.LIKE), now=1.0)
    assert action == SlideAction.NEXT


def test_latch_method_sets_latched_phase():
    sm = make_sm()
    track = HandTrack(track_id=0)
    track.phase = GesturePhase.ARMED
    sm.latch(track)
    assert track.phase == GesturePhase.LATCHED
    assert track.candidate == Gesture.NONE


def test_action_scissors_maps_to_previous():
    assert GESTURE_TO_ACTION[Gesture.SCISSORS] == SlideAction.PREVIOUS


def test_action_like_maps_to_next():
    assert GESTURE_TO_ACTION[Gesture.LIKE] == SlideAction.NEXT


def test_track_stays_armed_if_action_not_yet_dispatched():
    sm = make_sm()
    track = HandTrack(track_id=0)
    track.phase = GesturePhase.ARMED
    track.candidate = Gesture.SCISSORS
    action = sm.update(track, make_detection(Gesture.SCISSORS), now=1.0)
    assert action == SlideAction.PREVIOUS
    assert track.phase == GesturePhase.ARMED


def test_open_palm_stabilizing_to_armed_returns_blackout():
    sm = make_sm()
    track = HandTrack(track_id=0)
    sm.update(track, make_detection(Gesture.OPEN_PALM), now=0.0)
    assert track.phase == GesturePhase.STABILIZING
    assert track.candidate == Gesture.OPEN_PALM
    action = sm.update(track, make_detection(Gesture.OPEN_PALM), now=0.09)
    assert track.phase == GesturePhase.ARMED
    assert action == SlideAction.BLACKOUT


def test_open_palm_latched_blocks_retrigger():
    sm = make_sm()
    track = HandTrack(track_id=0)
    track.phase = GesturePhase.LATCHED
    action = sm.update(track, make_detection(Gesture.OPEN_PALM), now=1.0)
    assert action == SlideAction.NONE
    assert track.phase == GesturePhase.LATCHED


def test_open_palm_works_after_rearm():
    sm = make_sm()
    track = HandTrack(track_id=0)
    track.phase = GesturePhase.LATCHED
    sm.update(track, make_detection(Gesture.NONE), now=1.0)
    sm.update(track, make_detection(Gesture.NONE), now=1.20)
    assert track.phase == GesturePhase.IDLE
    sm.update(track, make_detection(Gesture.OPEN_PALM), now=1.20)
    action = sm.update(track, make_detection(Gesture.OPEN_PALM), now=1.30)
    assert action == SlideAction.BLACKOUT


def test_action_open_palm_maps_to_blackout():
    assert GESTURE_TO_ACTION[Gesture.OPEN_PALM] == SlideAction.BLACKOUT