from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum, auto


class Gesture(Enum):
    NONE = auto()
    SCISSORS = auto()
    LIKE = auto()
    OPEN_PALM = auto()


class SlideAction(Enum):
    NONE = auto()
    PREVIOUS = auto()
    NEXT = auto()
    BLACKOUT = auto()


class GesturePhase(Enum):
    IDLE = auto()
    STABILIZING = auto()
    ARMED = auto()
    LATCHED = auto()


GESTURE_TO_ACTION: dict[Gesture, SlideAction] = {
    Gesture.SCISSORS: SlideAction.PREVIOUS,
    Gesture.LIKE: SlideAction.NEXT,
    Gesture.OPEN_PALM: SlideAction.BLACKOUT,
}

ACTION_TO_KEY: dict[SlideAction, str] = {
    SlideAction.PREVIOUS: "left",
    SlideAction.NEXT: "right",
    SlideAction.BLACKOUT: "b",
}


@dataclass
class ScissorsDebug:
    index_extended: bool = False
    middle_extended: bool = False
    ring_folded: bool = False
    pinky_folded: bool = False
    tip_separation: float = 0.0
    spread_ratio: float = 0.0


@dataclass
class LikeDebug:
    vertical: bool = False
    folded_count: int = 0
    thumb_angle: float = 0.0
    thumb_extension: float = 0.0
    vertical_rise: float = 0.0
    horizontal_shift: float = 0.0


@dataclass
class OpenPalmDebug:
    index_extended: bool = False
    middle_extended: bool = False
    ring_extended: bool = False
    pinky_extended: bool = False
    thumb_extended: bool = False


@dataclass
class GestureResult:
    gesture: Gesture
    scissors: ScissorsDebug = field(default_factory=ScissorsDebug)
    like: LikeDebug = field(default_factory=LikeDebug)
    open_palm: OpenPalmDebug = field(default_factory=OpenPalmDebug)


@dataclass
class HandDetection:
    landmarks: list
    wrist_x: float
    wrist_y: float
    gesture_result: GestureResult
    raw_label: str = "Unknown"
    raw_score: float = 0.0


@dataclass
class HandTrack:
    track_id: int
    wrist_pos: tuple[float, float] | None = None
    last_seen: float = -999.0
    wrist_velocity: float = 0.0
    phase: GesturePhase = GesturePhase.IDLE
    candidate: Gesture = Gesture.NONE
    candidate_since: float = 0.0
    none_since: float = 0.0


@dataclass(frozen=True)
class RenderDetection:
    landmarks_xy: tuple[tuple[float, float], ...]
    wrist_x: float
    wrist_y: float
    gesture: Gesture
    raw_label: str = "Unknown"
    raw_score: float = 0.0


@dataclass(frozen=True)
class RenderTrack:
    track_id: int
    wrist_pos: tuple[float, float] | None = None
    phase: GesturePhase = GesturePhase.IDLE
    gesture: Gesture = Gesture.NONE
    candidate: Gesture = Gesture.NONE
    wrist_velocity: float = 0.0
    raw_label: str = "Unknown"


@dataclass(frozen=True)
class RenderSnapshot:
    tracks: tuple[RenderTrack, ...] = ()
    detections: tuple[RenderDetection, ...] = ()
    detection_track_map: dict[int, int] = field(default_factory=dict)
    last_action_text: str = ""
    last_action_time: float = 0.0
    result_age_ms: float = 0.0
    timestamp: float = 0.0