from __future__ import annotations
from .config import GestureConfig
from .geometry import distance_2d, xy_distance, angle_2d
from .hand_features import get_palm_scale, get_palm_center, is_finger_folded, is_finger_extended
from .models import Gesture, GestureResult, ScissorsDebug, LikeDebug, OpenPalmDebug


class GestureRecognizer:
    def __init__(self, cfg: GestureConfig) -> None:
        self._cfg = cfg

    def classify(self, landmarks) -> GestureResult:
        cfg = self._cfg
        palm_scale = get_palm_scale(landmarks)
        palm_center = get_palm_center(landmarks)

        # Evaluate 4 non-thumb fingers once to share across all detectors
        ext_index = is_finger_extended(landmarks, 5, 6, 7, 8, cfg, palm_scale, palm_center)
        ext_middle = is_finger_extended(landmarks, 9, 10, 11, 12, cfg, palm_scale, palm_center)
        ext_ring = is_finger_extended(landmarks, 13, 14, 15, 16, cfg, palm_scale, palm_center)
        ext_pinky = is_finger_extended(landmarks, 17, 18, 19, 20, cfg, palm_scale, palm_center)

        fold_index = is_finger_folded(landmarks, 5, 6, 7, 8, cfg)
        fold_middle = is_finger_folded(landmarks, 9, 10, 11, 12, cfg)
        fold_ring = is_finger_folded(landmarks, 13, 14, 15, 16, cfg)
        fold_pinky = is_finger_folded(landmarks, 17, 18, 19, 20, cfg)

        open_palm_debug, open_palm_detected = self._detect_open_palm(
            landmarks,
            palm_scale,
            palm_center,
            extended_fingers=(ext_index, ext_middle, ext_ring, ext_pinky),
        )
        scissors_debug, scissors_detected = self._detect_scissors(
            landmarks,
            palm_scale,
            palm_center,
            ext_index=ext_index,
            ext_middle=ext_middle,
            fold_ring=fold_ring,
            fold_pinky=fold_pinky,
        )
        like_debug, like_detected = self._detect_like(
            landmarks,
            palm_scale,
            palm_center,
            folded_fingers=(fold_index, fold_middle, fold_ring, fold_pinky),
        )

        if open_palm_detected:
            gesture = Gesture.OPEN_PALM
        elif like_detected:
            gesture = Gesture.LIKE
        elif scissors_detected:
            gesture = Gesture.SCISSORS
        else:
            gesture = Gesture.NONE

        return GestureResult(
            gesture=gesture,
            scissors=scissors_debug,
            like=like_debug,
            open_palm=open_palm_debug,
        )

    def _detect_scissors(
        self,
        landmarks,
        palm_scale: float | None = None,
        palm_center: tuple[float, float] | None = None,
        ext_index: bool | None = None,
        ext_middle: bool | None = None,
        fold_ring: bool | None = None,
        fold_pinky: bool | None = None,
    ) -> tuple[ScissorsDebug, bool]:
        cfg = self._cfg
        if palm_scale is None:
            palm_scale = get_palm_scale(landmarks)
        if palm_center is None:
            palm_center = get_palm_center(landmarks)

        index_extended = ext_index if ext_index is not None else is_finger_extended(landmarks, 5, 6, 7, 8, cfg, palm_scale, palm_center)
        middle_extended = ext_middle if ext_middle is not None else is_finger_extended(landmarks, 9, 10, 11, 12, cfg, palm_scale, palm_center)
        ring_folded = fold_ring if fold_ring is not None else is_finger_folded(landmarks, 13, 14, 15, 16, cfg)
        pinky_folded = fold_pinky if fold_pinky is not None else is_finger_folded(landmarks, 17, 18, 19, 20, cfg)

        index_tip = landmarks[8]
        middle_tip = landmarks[12]
        index_mcp = landmarks[5]
        middle_mcp = landmarks[9]

        tip_dist = distance_2d(index_tip, middle_tip)
        tip_separation = tip_dist / palm_scale
        base_separation = max(distance_2d(index_mcp, middle_mcp), 1e-6)
        spread_ratio = tip_dist / base_separation

        separated = (
            tip_separation >= cfg.scissors_min_tip_separation
            and spread_ratio >= cfg.scissors_min_base_separation_ratio
        )
        detected = index_extended and middle_extended and ring_folded and pinky_folded and separated

        return ScissorsDebug(
            index_extended=index_extended,
            middle_extended=middle_extended,
            ring_folded=ring_folded,
            pinky_folded=pinky_folded,
            tip_separation=tip_separation,
            spread_ratio=spread_ratio,
        ), detected

    def _detect_like(
        self,
        landmarks,
        palm_scale: float | None = None,
        palm_center: tuple[float, float] | None = None,
        folded_fingers: tuple[bool, bool, bool, bool] | None = None,
    ) -> tuple[LikeDebug, bool]:
        cfg = self._cfg
        if palm_scale is None:
            palm_scale = get_palm_scale(landmarks)
        if palm_center is None:
            palm_x, palm_y = get_palm_center(landmarks)
        else:
            palm_x, palm_y = palm_center

        thumb_mcp = landmarks[2]
        thumb_ip = landmarks[3]
        thumb_tip = landmarks[4]
        index_mcp = landmarks[5]

        thumb_angle = angle_2d(thumb_mcp, thumb_ip, thumb_tip)
        thumb_mcp_extension = distance_2d(thumb_mcp, thumb_tip) / palm_scale
        thumb_palm_extension = xy_distance(thumb_tip.x, thumb_tip.y, palm_x, palm_y) / palm_scale

        vertical_rise = (thumb_mcp.y - thumb_tip.y) / palm_scale
        horizontal_shift = abs(thumb_tip.x - thumb_mcp.x) / palm_scale

        above_palm = (palm_y - thumb_tip.y) / palm_scale >= cfg.thumb_above_palm_margin
        above_index_mcp = thumb_tip.y < index_mcp.y

        vertical = (
            vertical_rise >= cfg.thumb_min_vertical_rise
            and vertical_rise >= horizontal_shift * cfg.thumb_vertical_ratio
            and above_palm
            and above_index_mcp
        )

        if folded_fingers is not None:
            index_folded, middle_folded, ring_folded, pinky_folded = folded_fingers
        else:
            index_folded = is_finger_folded(landmarks, 5, 6, 7, 8, cfg)
            middle_folded = is_finger_folded(landmarks, 9, 10, 11, 12, cfg)
            ring_folded = is_finger_folded(landmarks, 13, 14, 15, 16, cfg)
            pinky_folded = is_finger_folded(landmarks, 17, 18, 19, 20, cfg)

        folded_count = sum([index_folded, middle_folded, ring_folded, pinky_folded])

        detected = (
            thumb_angle >= cfg.thumb_min_angle
            and thumb_mcp_extension >= cfg.thumb_min_mcp_extension
            and thumb_palm_extension >= cfg.thumb_min_palm_extension
            and vertical
            and index_folded
            and middle_folded
            and ring_folded
            and pinky_folded
        )

        return LikeDebug(
            vertical=vertical,
            folded_count=folded_count,
            thumb_angle=thumb_angle,
            thumb_extension=thumb_palm_extension,
            vertical_rise=vertical_rise,
            horizontal_shift=horizontal_shift,
        ), detected

    def _detect_open_palm(
        self,
        landmarks,
        palm_scale: float | None = None,
        palm_center: tuple[float, float] | None = None,
        extended_fingers: tuple[bool, bool, bool, bool] | None = None,
    ) -> tuple[OpenPalmDebug, bool]:
        """Detect open palm (all 5 fingers spread out / extended)."""
        cfg = self._cfg
        if palm_scale is None:
            palm_scale = get_palm_scale(landmarks)
        if palm_center is None:
            palm_x, palm_y = get_palm_center(landmarks)
        else:
            palm_x, palm_y = palm_center

        if extended_fingers is not None:
            index_ext, middle_ext, ring_ext, pinky_ext = extended_fingers
        else:
            index_ext = is_finger_extended(landmarks, 5, 6, 7, 8, cfg, palm_scale, (palm_x, palm_y))
            middle_ext = is_finger_extended(landmarks, 9, 10, 11, 12, cfg, palm_scale, (palm_x, palm_y))
            ring_ext = is_finger_extended(landmarks, 13, 14, 15, 16, cfg, palm_scale, (palm_x, palm_y))
            pinky_ext = is_finger_extended(landmarks, 17, 18, 19, 20, cfg, palm_scale, (palm_x, palm_y))

        # Thumb: tip must be far from palm center (extended outward) and above wrist
        thumb_tip = landmarks[4]
        thumb_tip_palm_dist = xy_distance(thumb_tip.x, thumb_tip.y, palm_x, palm_y) / palm_scale
        wrist = landmarks[0]
        thumb_extended = (
            thumb_tip_palm_dist >= cfg.thumb_min_palm_extension
            and thumb_tip.y < wrist.y
        )

        detected = index_ext and middle_ext and ring_ext and pinky_ext and thumb_extended

        return OpenPalmDebug(
            index_extended=index_ext,
            middle_extended=middle_ext,
            ring_extended=ring_ext,
            pinky_extended=pinky_ext,
            thumb_extended=thumb_extended,
        ), detected