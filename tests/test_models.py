from hand_controller.models import (
    Gesture,
    SlideAction,
    GesturePhase,
    GESTURE_TO_ACTION,
    ACTION_TO_KEY,
    GestureResult,
    HandDetection,
    HandTrack,
)


def test_gesture_enum_distinct():
    assert Gesture.NONE != Gesture.SCISSORS
    assert Gesture.NONE != Gesture.LIKE
    assert Gesture.SCISSORS != Gesture.LIKE


def test_slide_action_enum_distinct():
    assert SlideAction.NONE != SlideAction.PREVIOUS
    assert SlideAction.NONE != SlideAction.NEXT
    assert SlideAction.PREVIOUS != SlideAction.NEXT


def test_gesture_to_action_mapping():
    assert GESTURE_TO_ACTION[Gesture.SCISSORS] == SlideAction.PREVIOUS
    assert GESTURE_TO_ACTION[Gesture.LIKE] == SlideAction.NEXT
    assert GESTURE_TO_ACTION[Gesture.OPEN_PALM] == SlideAction.BLACKOUT


def test_action_to_key_mapping():
    assert ACTION_TO_KEY[SlideAction.PREVIOUS] == "left"
    assert ACTION_TO_KEY[SlideAction.NEXT] == "right"
    assert ACTION_TO_KEY[SlideAction.BLACKOUT] == "b"


def test_gesture_result_defaults():
    gr = GestureResult(gesture=Gesture.NONE)
    assert gr.gesture == Gesture.NONE
    assert not gr.scissors.index_extended
    assert not gr.like.vertical
    assert not gr.open_palm.thumb_extended


def test_hand_detection_defaults():
    gr = GestureResult(gesture=Gesture.NONE)
    det = HandDetection(landmarks=[], wrist_x=0.5, wrist_y=0.5, gesture_result=gr)
    assert det.raw_label == "Unknown"
    assert det.raw_score == 0.0


def test_hand_track_defaults():
    track = HandTrack(track_id=0)
    assert track.phase == GesturePhase.IDLE
    assert track.candidate == Gesture.NONE
    assert track.wrist_pos is None
    assert track.last_seen == -999.0
    assert track.wrist_velocity == 0.0


def test_hand_track_two_instances_independent():
    t0 = HandTrack(track_id=0)
    t1 = HandTrack(track_id=1)
    t0.wrist_velocity = 1.5
    assert t0.wrist_velocity == 1.5
    assert t1.wrist_velocity == 0.0