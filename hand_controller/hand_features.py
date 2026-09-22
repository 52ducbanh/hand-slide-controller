from __future__ import annotations
from .geometry import distance_2d, xy_distance, angle_2d
from .config import GestureConfig


def get_palm_scale(landmarks) -> float:
    wrist = landmarks[0]
    index_mcp = landmarks[5]
    middle_mcp = landmarks[9]
    pinky_mcp = landmarks[17]
    palm_length = distance_2d(wrist, middle_mcp)
    palm_width = distance_2d(index_mcp, pinky_mcp)
    return max((palm_length + palm_width) / 2, 0.012)


def get_palm_center(landmarks) -> tuple[float, float]:
    indices = [5, 9, 13, 17]
    x = sum(landmarks[i].x for i in indices) / len(indices)
    y = sum(landmarks[i].y for i in indices) / len(indices)
    return x, y


def is_finger_folded(
    landmarks,
    mcp_i: int,
    pip_i: int,
    dip_i: int,
    tip_i: int,
    cfg: GestureConfig,
) -> bool:
    wrist = landmarks[0]
    mcp = landmarks[mcp_i]
    pip = landmarks[pip_i]
    dip = landmarks[dip_i]
    tip = landmarks[tip_i]

    pip_angle = angle_2d(mcp, pip, dip)
    dip_angle = angle_2d(pip, dip, tip)
    mcp_to_pip = distance_2d(mcp, pip)

    if mcp_to_pip < 1e-6:
        return False

    tip_ratio = distance_2d(mcp, tip) / mcp_to_pip
    bent = pip_angle < cfg.finger_folded_max_pip_angle or dip_angle < cfg.finger_folded_max_dip_angle
    compact = tip_ratio < cfg.finger_tip_mcp_ratio
    curled = distance_2d(tip, wrist) < distance_2d(pip, wrist) * 1.28

    return curled or (bent and compact)


def is_finger_extended(
    landmarks,
    mcp_i: int,
    pip_i: int,
    dip_i: int,
    tip_i: int,
    cfg: GestureConfig,
    palm_scale: float | None = None,
    palm_center: tuple[float, float] | None = None,
) -> bool:
    if palm_scale is None:
        palm_scale = get_palm_scale(landmarks)
    if palm_center is None:
        palm_x, palm_y = get_palm_center(landmarks)
    else:
        palm_x, palm_y = palm_center
    mcp = landmarks[mcp_i]

    pip = landmarks[pip_i]
    dip = landmarks[dip_i]
    tip = landmarks[tip_i]

    pip_angle = angle_2d(mcp, pip, dip)
    dip_angle = angle_2d(pip, dip, tip)
    mcp_to_pip = distance_2d(mcp, pip)

    if mcp_to_pip < 1e-6:
        return False

    extension = distance_2d(mcp, tip) / mcp_to_pip
    palm_distance = xy_distance(tip.x, tip.y, palm_x, palm_y) / palm_scale

    return (
        pip_angle >= cfg.extended_min_pip_angle
        and dip_angle >= cfg.extended_min_dip_angle
        and extension >= cfg.extended_min_extension
        and palm_distance >= cfg.extended_min_palm_distance
    )
