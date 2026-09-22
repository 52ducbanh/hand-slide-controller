from __future__ import annotations
import threading
import time
from typing import NamedTuple
from hand_controller.app import _AsyncSharedState, _FrameMeta, _ResultPacket, App
from hand_controller.config import AppConfig


def test_monotonic_timestamp_enforcement() -> None:
    last_timestamp_ms = -1
    timestamps = []
    raw_readings = [100, 100, 100, 102, 101, 105, 105, 106]
    for now_ms in raw_readings:
        timestamp_ms = max(now_ms, last_timestamp_ms + 1)
        last_timestamp_ms = timestamp_ms
        timestamps.append(timestamp_ms)

    assert timestamps == [100, 101, 102, 103, 104, 105, 106, 107]
    for i in range(1, len(timestamps)):
        assert timestamps[i] > timestamps[i - 1], "Timestamps must be strictly increasing"


def test_async_state_bounded_storage_and_eviction() -> None:
    state = _AsyncSharedState()

    with state.lock:
        for i in range(130):
            ts = i * 33 + 1
            if len(state.pending_meta) >= 120:
                oldest = sorted(state.pending_meta.keys())[:30]
                for k in oldest:
                    del state.pending_meta[k]
                    state.metadata_eviction += 1
            state.pending_meta[ts] = _FrameMeta(time.perf_counter(), time.perf_counter())

    assert len(state.pending_meta) <= 120
    assert state.metadata_eviction == 30


def test_async_callback_prunes_older_pending_frames_as_input_drops() -> None:
    app = App(AppConfig(running_mode="LIVE_STREAM_WORKER"))

    # Register 5 submitted frames
    with app._shared.lock:
        for i in range(1, 6):
            ts = i * 33
            app._shared.pending_meta[ts] = _FrameMeta(1.0 * i, 1.0 * i + 0.001)

    # Callback arrives for frame 3 (timestamp 99)
    # Frames 1 and 2 (timestamps 33, 66) should be pruned as mediapipe_input_drop
    mock_result = NamedTuple("MockRes", [("hand_landmarks", list), ("handedness", list)])([], [])
    app._on_async_result(mock_result, None, 99)

    with app._shared.lock:
        assert 33 not in app._shared.pending_meta
        assert 66 not in app._shared.pending_meta
        assert 99 not in app._shared.pending_meta
        assert 132 in app._shared.pending_meta
        assert 165 in app._shared.pending_meta
        assert app._shared.mediapipe_input_drop == 2
        assert app._shared.latest_packet is not None
        assert app._shared.latest_packet.timestamp_ms == 99


def test_control_overwrite_drop_tracking() -> None:
    app = App(AppConfig(running_mode="LIVE_STREAM_WORKER"))
    mock_result = NamedTuple("MockRes", [("hand_landmarks", list), ("handedness", list)])([], [])

    # First callback
    app._on_async_result(mock_result, None, 100)
    assert app._shared.control_overwrite_drop == 0

    # Second callback arrives before Control Worker consumes the first one
    app._on_async_result(mock_result, None, 133)
    assert app._shared.control_overwrite_drop == 1
    assert app._shared.latest_packet.timestamp_ms == 133


def test_async_callback_ignores_when_closed() -> None:
    app = App(AppConfig(running_mode="LIVE_STREAM_WORKER"))
    app._shared.is_closed = True

    mock_result = NamedTuple("MockRes", [("hand_landmarks", list), ("handedness", list)])([], [])
    app._on_async_result(mock_result, None, 100)

    assert app._shared.latest_packet is None
    assert app._shared.callback_count == 0


def test_async_callback_handles_exception_safely() -> None:
    app = App(AppConfig(running_mode="LIVE_STREAM_WORKER"))
    app._shared.pending_meta = None  # type: ignore

    mock_result = NamedTuple("MockRes", [("hand_landmarks", list), ("handedness", list)])([], [])
    app._on_async_result(mock_result, None, 100)

    assert app._shared.callback_error_count == 1
