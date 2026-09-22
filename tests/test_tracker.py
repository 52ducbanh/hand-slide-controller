from unittest.mock import MagicMock
from hand_controller.config import TrackingConfig
from hand_controller.tracker import HandTracker
from hand_controller.models import HandDetection, GestureResult, Gesture, GesturePhase


def make_detection(wx: float, wy: float) -> HandDetection:
    gr = GestureResult(gesture=Gesture.NONE)
    lm = MagicMock()
    lm.x = wx
    lm.y = wy
    lm.z = 0.0
    return HandDetection(landmarks=[lm] * 21, wrist_x=wx, wrist_y=wy, gesture_result=gr)



def test_no_detections_returns_empty():
    tracker = HandTracker(TrackingConfig())
    assert tracker.assign([], now=0.0) == {}


def test_single_detection_new_track_gets_slot():
    tracker = HandTracker(TrackingConfig())
    assignments = tracker.assign([make_detection(0.5, 0.5)], now=0.0)
    assert 0 in assignments
    assert assignments[0] in (0, 1)


def test_single_detection_reconnects_to_near_track():
    tracker = HandTracker(TrackingConfig())
    tracker.tracks[0].wrist_pos = (0.5, 0.5)
    tracker.tracks[0].last_seen = 0.0
    assignments = tracker.assign([make_detection(0.51, 0.51)], now=0.1)
    assert assignments[0] == 0


def test_single_detection_does_not_reconnect_to_far_track():
    tracker = HandTracker(TrackingConfig())
    tracker.tracks[0].wrist_pos = (0.0, 0.0)
    tracker.tracks[0].last_seen = 0.0
    assignments = tracker.assign([make_detection(0.9, 0.9)], now=0.1)
    assert 0 in assignments


def test_track_expires_after_reset_timeout():
    tracker = HandTracker(TrackingConfig())
    tracker.tracks[0].wrist_pos = (0.5, 0.5)
    tracker.tracks[0].last_seen = 0.0
    tracker.expire_lost_tracks(assigned_ids=set(), now=1.0)
    assert tracker.tracks[0].wrist_pos is None
    assert tracker.tracks[0].phase == GesturePhase.IDLE


def test_track_not_expired_within_timeout():
    tracker = HandTracker(TrackingConfig())
    tracker.tracks[0].wrist_pos = (0.5, 0.5)
    tracker.tracks[0].last_seen = 0.0
    tracker.expire_lost_tracks(assigned_ids=set(), now=0.5)
    assert tracker.tracks[0].wrist_pos == (0.5, 0.5)


def test_assigned_track_not_expired():
    tracker = HandTracker(TrackingConfig())
    tracker.tracks[0].wrist_pos = (0.5, 0.5)
    tracker.tracks[0].last_seen = 0.0
    tracker.expire_lost_tracks(assigned_ids={0}, now=2.0)
    assert tracker.tracks[0].wrist_pos == (0.5, 0.5)


def test_two_detections_correct_assignment():
    tracker = HandTracker(TrackingConfig())
    tracker.tracks[0].wrist_pos = (0.1, 0.5)
    tracker.tracks[0].last_seen = 0.0
    tracker.tracks[1].wrist_pos = (0.9, 0.5)
    tracker.tracks[1].last_seen = 0.0
    assignments = tracker.assign([make_detection(0.12, 0.5), make_detection(0.88, 0.5)], now=0.1)
    assert assignments[0] == 0
    assert assignments[1] == 1


def test_two_detections_swapped_assignment():
    tracker = HandTracker(TrackingConfig())
    tracker.tracks[0].wrist_pos = (0.1, 0.5)
    tracker.tracks[0].last_seen = 0.0
    tracker.tracks[1].wrist_pos = (0.9, 0.5)
    tracker.tracks[1].last_seen = 0.0
    assignments = tracker.assign([make_detection(0.88, 0.5), make_detection(0.12, 0.5)], now=0.1)
    assert assignments[0] == 1
    assert assignments[1] == 0


def test_update_position_updates_wrist():
    tracker = HandTracker(TrackingConfig())
    track = tracker.tracks[0]
    tracker.update_position(track, make_detection(0.3, 0.7), now=1.5)
    assert track.wrist_pos == (0.3, 0.7)
    assert track.last_seen == 1.5