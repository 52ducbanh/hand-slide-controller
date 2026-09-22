from __future__ import annotations
from .config import TrackingConfig
from .models import (
    HandTrack,
    HandDetection,
    Gesture,
    SlideAction,
    GesturePhase,
    GESTURE_TO_ACTION,
)


class GestureStateMachine:
    def __init__(self, cfg: TrackingConfig) -> None:
        self._cfg = cfg

    def update(self, track: HandTrack, detection: HandDetection, now: float) -> SlideAction:
        gesture = detection.gesture_result.gesture

        if track.phase == GesturePhase.LATCHED:
            return self._handle_latched(track, gesture, now)

        if gesture == Gesture.NONE:
            return self._handle_none(track)

        if gesture != track.candidate:
            return self._start_candidate(track, gesture, now)

        if track.phase == GesturePhase.STABILIZING:
            if track.wrist_velocity > self._cfg.max_trigger_velocity:
                track.candidate_since = now
                return SlideAction.NONE
            if now - track.candidate_since >= self._cfg.gesture_stable_time:
                track.phase = GesturePhase.ARMED
            else:
                return SlideAction.NONE

        if track.phase == GesturePhase.ARMED:
            if track.wrist_velocity > self._cfg.max_trigger_velocity:
                return SlideAction.NONE
            return GESTURE_TO_ACTION.get(gesture, SlideAction.NONE)

        return SlideAction.NONE

    def latch(self, track: HandTrack) -> None:
        track.phase = GesturePhase.LATCHED
        track.candidate = Gesture.NONE
        track.none_since = 0.0

    def _handle_latched(self, track: HandTrack, gesture: Gesture, now: float) -> SlideAction:
        if gesture == Gesture.NONE:
            if track.none_since == 0.0:
                track.none_since = now
            if now - track.none_since >= self._cfg.rearm_stable_time:
                track.phase = GesturePhase.IDLE
                track.candidate = Gesture.NONE
                track.candidate_since = 0.0
                track.none_since = 0.0
        else:
            track.none_since = 0.0

        return SlideAction.NONE

    def _handle_none(self, track: HandTrack) -> SlideAction:
        track.candidate = Gesture.NONE
        track.candidate_since = 0.0
        track.none_since = 0.0
        track.phase = GesturePhase.IDLE
        return SlideAction.NONE

    def _start_candidate(self, track: HandTrack, gesture: Gesture, now: float) -> SlideAction:
        track.candidate = gesture
        track.candidate_since = now
        track.none_since = 0.0
        track.phase = GesturePhase.STABILIZING
        return SlideAction.NONE