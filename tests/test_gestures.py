from unittest.mock import MagicMock
from hand_controller.gestures import GestureRecognizer
from hand_controller.config import GestureConfig
from hand_controller.hand_features import get_palm_scale, get_palm_center
from hand_controller.models import Gesture


def make_landmark(x: float = 0.5, y: float = 0.5, z: float = 0.0):
    lm = MagicMock()
    lm.x = x
    lm.y = y
    lm.z = z
    return lm


def test_classify_flat_landmarks_is_none():
    rec = GestureRecognizer(GestureConfig())
    landmarks = [make_landmark()] * 21
    result = rec.classify(landmarks)
    assert result.gesture == Gesture.NONE


def test_palm_scale_positive_minimum():
    landmarks = [make_landmark(0.5, 0.5)] * 21
    scale = get_palm_scale(landmarks)
    assert scale >= 0.012


def test_palm_center_correctness():
    landmarks = [make_landmark(0.0, 0.0)] * 21
    # indices 5, 9, 13, 17
    landmarks[5] = make_landmark(0.2, 0.4)
    landmarks[9] = make_landmark(0.4, 0.6)
    landmarks[13] = make_landmark(0.6, 0.8)
    landmarks[17] = make_landmark(0.8, 1.0)
    cx, cy = get_palm_center(landmarks)
    assert abs(cx - 0.5) < 1e-6
    assert abs(cy - 0.7) < 1e-6


def test_detector_cached_metrics_consistency():
    landmarks = [make_landmark(0.05 * i, 0.03 * i) for i in range(21)]
    rec = GestureRecognizer(GestureConfig())
    scale = get_palm_scale(landmarks)
    center = get_palm_center(landmarks)

    _, det1 = rec._detect_scissors(landmarks)
    _, det2 = rec._detect_scissors(landmarks, scale, center)
    assert det1 == det2

    _, lk1 = rec._detect_like(landmarks)
    _, lk2 = rec._detect_like(landmarks, scale, center)
    assert lk1 == lk2

    _, op1 = rec._detect_open_palm(landmarks)
    _, op2 = rec._detect_open_palm(landmarks, scale, center)
    assert op1 == op2
