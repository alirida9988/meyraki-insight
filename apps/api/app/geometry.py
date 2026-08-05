"""Deterministic polygon math over normalized (0-1) ZoneGraph coordinates."""

from meyraki_contracts import Point


def polygon_area(points: list[Point]) -> float:
    """Shoelace area in normalized units (fraction of the plan sheet)."""
    n = len(points)
    acc = 0.0
    for i in range(n):
        j = (i + 1) % n
        acc += points[i].x * points[j].y - points[j].x * points[i].y
    return abs(acc) / 2.0


def centroid(points: list[Point]) -> tuple[float, float]:
    xs = sum(p.x for p in points) / len(points)
    ys = sum(p.y for p in points) / len(points)
    return xs, ys
