from __future__ import annotations
import math


def distance_2d(a, b) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def xy_distance(x1: float, y1: float, x2: float, y2: float) -> float:
    return math.hypot(x1 - x2, y1 - y2)


def angle_2d(a, b, c) -> float:
    bax = a.x - b.x
    bay = a.y - b.y
    bcx = c.x - b.x
    bcy = c.y - b.y
    ba_len = math.hypot(bax, bay)
    bc_len = math.hypot(bcx, bcy)
    if ba_len < 1e-6 or bc_len < 1e-6:
        return 0.0
    cosine = max(-1.0, min(1.0, (bax * bcx + bay * bcy) / (ba_len * bc_len)))
    return math.degrees(math.acos(cosine))