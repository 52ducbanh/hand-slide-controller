from __future__ import annotations
from typing import Container
from .config import TrackingConfig
from .geometry import xy_distance
from .hand_features import get_palm_scale
from .models import HandTrack, HandDetection, GesturePhase, Gesture


class HandTracker:
    def __init__(self, cfg: TrackingConfig) -> None:
        self._cfg = cfg
        self.tracks: list[HandTrack] = [HandTrack(track_id=0), HandTrack(track_id=1)]

    def assign(self, detections: list[HandDetection], now: float) -> dict[int, int]:
        n = len(detections)
        if n == 0:
            return {}

        recent_ids = [
            i for i in range(2)
            if (
                self.tracks[i].wrist_pos is not None
                and now - self.tracks[i].last_seen <= self._cfg.reacquire_timeout
            )
        ]

        if n == 1:
            return self._assign_single(detections[0], recent_ids)
        return self._assign_pair(detections[0], detections[1], recent_ids)

    def expire_lost_tracks(self, assigned_ids: Container[int], now: float) -> None:
        for track_id in range(2):
            if track_id in assigned_ids:
                continue
            track = self.tracks[track_id]
            if track.wrist_pos is not None and now - track.last_seen > self._cfg.reset_timeout:
                self._hard_reset(track)


    def update_position(self, track: HandTrack, detection: HandDetection, now: float) -> None:
        if track.wrist_pos is not None:
            dt = now - track.last_seen
            if 0 < dt < 0.25:
                palm_scale = get_palm_scale(detection.landmarks)
                dist = xy_distance(track.wrist_pos[0], track.wrist_pos[1], detection.wrist_x, detection.wrist_y)
                raw_velocity = (dist / palm_scale) / dt
                track.wrist_velocity = 0.6 * raw_velocity + 0.4 * track.wrist_velocity
            else:
                track.wrist_velocity = 0.0
        else:
            track.wrist_velocity = 0.0

        track.wrist_pos = (detection.wrist_x, detection.wrist_y)
        track.last_seen = now

    def _assign_single(self, detection: HandDetection, recent_ids: list[int]) -> dict[int, int]:
        best_track, best_dist = self._best_match(detection, recent_ids)
        if best_track is not None and best_dist <= self._cfg.max_match_distance:
            return {0: best_track}
        available = [i for i in range(2) if i not in recent_ids]
        slot = available[0] if available else min(range(2), key=lambda i: self.tracks[i].last_seen)
        self._hard_reset(self.tracks[slot])
        return {0: slot}

    def _assign_pair(
        self,
        d0: HandDetection,
        d1: HandDetection,
        recent_ids: list[int],
    ) -> dict[int, int]:
        if len(recent_ids) >= 2:
            t0, t1 = recent_ids[0], recent_ids[1]
            direct = self._dist(self.tracks[t0], d0) + self._dist(self.tracks[t1], d1)
            swapped = self._dist(self.tracks[t0], d1) + self._dist(self.tracks[t1], d0)
            threshold = self._cfg.max_match_distance * 2
            if min(direct, swapped) <= threshold:
                if direct <= swapped:
                    return {0: t0, 1: t1}
                return {0: t1, 1: t0}
            return self._reset_both_tracks()


        if len(recent_ids) == 1:
            known = recent_ids[0]
            other = 1 - known
            dist_d0 = self._dist(self.tracks[known], d0)
            dist_d1 = self._dist(self.tracks[known], d1)
            if min(dist_d0, dist_d1) <= self._cfg.max_match_distance:
                self._hard_reset(self.tracks[other])
                if dist_d0 <= dist_d1:
                    return {0: known, 1: other}
                return {1: known, 0: other}
            return self._reset_both_tracks()

        return self._reset_both_tracks()

    def _reset_both_tracks(self) -> dict[int, int]:
        self._hard_reset(self.tracks[0])
        self._hard_reset(self.tracks[1])
        return {0: 0, 1: 1}


    def _best_match(
        self,
        detection: HandDetection,
        candidates: list[int],
    ) -> tuple[int | None, float]:
        best_track = None
        best_dist = 999.0
        for tid in candidates:
            d = self._dist(self.tracks[tid], detection)
            if d < best_dist:
                best_dist = d
                best_track = tid
        return best_track, best_dist

    def _dist(self, track: HandTrack, detection: HandDetection) -> float:
        if track.wrist_pos is None:
            return 999.0
        return xy_distance(
            track.wrist_pos[0], track.wrist_pos[1],
            detection.wrist_x, detection.wrist_y,
        )

    @staticmethod
    def _hard_reset(track: HandTrack) -> None:
        track.wrist_pos = None
        track.last_seen = -999.0
        track.wrist_velocity = 0.0
        track.phase = GesturePhase.IDLE
        track.candidate = Gesture.NONE
        track.candidate_since = 0.0
        track.none_since = 0.0