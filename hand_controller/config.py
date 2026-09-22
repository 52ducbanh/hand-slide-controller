from __future__ import annotations
import sys
import os
from dataclasses import dataclass, field


def get_default_model_path() -> str:
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, "hand_landmarker.task")
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    local_path = os.path.join(base_dir, "hand_landmarker.task")
    if os.path.exists(local_path):
        return local_path
    return "hand_landmarker.task"


@dataclass(frozen=True)
class CameraConfig:
    index: int = 0
    width: int = 1280
    height: int = 720
    fps: int = 30


@dataclass(frozen=True)
class TrackingConfig:
    reacquire_timeout: float = 0.40
    reset_timeout: float = 0.75
    max_match_distance: float = 0.30
    gesture_stable_time: float = 0.08
    rearm_stable_time: float = 0.15
    max_trigger_velocity: float = 0.80


@dataclass(frozen=True)
class GestureConfig:
    thumb_min_angle: float = 118.0
    thumb_min_mcp_extension: float = 0.28
    thumb_min_palm_extension: float = 0.44
    thumb_vertical_ratio: float = 1.00
    thumb_min_vertical_rise: float = 0.20
    thumb_above_palm_margin: float = 0.10

    extended_min_pip_angle: float = 142.0
    extended_min_dip_angle: float = 138.0
    extended_min_extension: float = 1.55
    extended_min_palm_distance: float = 0.58

    scissors_min_tip_separation: float = 0.30
    scissors_min_base_separation_ratio: float = 1.10

    finger_folded_max_pip_angle: float = 168.0
    finger_folded_max_dip_angle: float = 170.0
    finger_tip_mcp_ratio: float = 2.50


@dataclass(frozen=True)
class AppConfig:
    camera: CameraConfig = field(default_factory=CameraConfig)
    tracking: TrackingConfig = field(default_factory=TrackingConfig)
    gesture: GestureConfig = field(default_factory=GestureConfig)
    model_path: str = field(default_factory=get_default_model_path)
    global_action_cooldown: float = 0.35
    num_hands: int = 2
    min_hand_detection_confidence: float = 0.25
    min_hand_presence_confidence: float = 0.25
    min_tracking_confidence: float = 0.25
    debug: bool = True
    audio_feedback: bool = False
    preview_enabled: bool = True
    running_mode: str = "LIVE_STREAM_WORKER"