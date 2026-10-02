"""Unit tests for App dependency injection and refactored pipeline helper."""

from __future__ import annotations
from unittest.mock import MagicMock
import pytest

from hand_controller.config import AppConfig, TrackingConfig, GestureConfig
from hand_controller.gestures import GestureRecognizer
from hand_controller.tracker import HandTracker
from hand_controller.state_machine import GestureStateMachine
from hand_controller.actions import ActionDispatcher
from hand_controller.renderer import Renderer, RenderTheme
from hand_controller.camera import CameraSource
from hand_controller.app import App, SW_MINIMIZE


def test_app_constants():
    assert SW_MINIMIZE == 6
    assert isinstance(RenderTheme.COLOR_LANDMARK, tuple)
    assert len(RenderTheme.COLOR_LANDMARK) == 3
    assert isinstance(RenderTheme.COLOR_BANNER_BG, tuple)


def test_app_dependency_injection():
    cfg = AppConfig()
    custom_recognizer = GestureRecognizer(cfg.gesture)
    custom_tracker = HandTracker(cfg.tracking)
    custom_state_machine = GestureStateMachine(cfg.tracking)
    custom_dispatcher = ActionDispatcher(cooldown=0.5)
    custom_renderer = Renderer(debug=False)
    mock_camera = MagicMock(spec=CameraSource)

    app = App(
        cfg,
        recognizer=custom_recognizer,
        tracker=custom_tracker,
        state_machine=custom_state_machine,
        dispatcher=custom_dispatcher,
        renderer=custom_renderer,
        camera_source=mock_camera,
    )

    assert app._recognizer is custom_recognizer
    assert app._tracker is custom_tracker
    assert app._state_machine is custom_state_machine
    assert app._dispatcher is custom_dispatcher
    assert app._renderer is custom_renderer
    assert app._setup_camera() is mock_camera


def test_app_process_frame_detections_empty_result():
    app = App(AppConfig())
    mock_result = MagicMock()
    mock_result.hand_landmarks = None

    detections, track_map = app._process_frame_detections(mock_result, timestamp_sec=1.0)
    assert detections == []
    assert track_map == {}
