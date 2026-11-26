"""Small geometry helpers for zones."""

from __future__ import annotations

from collections.abc import Sequence

Point = tuple[float, float]


def point_in_polygon(x: float, y: float, poly: Sequence[Point]) -> bool:
    """Even-odd rule ray casting. Points on an edge may fall either way."""
    inside = False
    n = len(poly)
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y):
            x_cross = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def foot_point(box: Sequence[float], frame_size: tuple[int, int]) -> Point:
    """Bottom centre of a pixel ``xyxy`` box, normalised to 0-1 by the frame size."""
    w, h = frame_size
    return ((box[0] + box[2]) / 2 / w, box[3] / h)
