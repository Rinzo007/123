from __future__ import annotations

from dataclasses import dataclass
from math import hypot, isfinite


@dataclass(frozen=True, slots=True)
class Point:
    x: float
    y: float

    def __post_init__(self) -> None:
        if not (isfinite(self.x) and isfinite(self.y)):
            raise ValueError("Point coordinates must be finite")


@dataclass(frozen=True, slots=True)
class LineString:
    points: tuple[Point, ...]

    def __post_init__(self) -> None:
        if len(self.points) < 2:
            raise ValueError("LineString requires at least two points")

    @property
    def length(self) -> float:
        return sum(
            hypot(b.x - a.x, b.y - a.y)
            for a, b in zip(self.points, self.points[1:])
        )


def circumradius_m(a: Point, b: Point, c: Point) -> float:
    """Return the circumcircle radius through three metric points.

    Collinear or repeated points produce infinity because they do not impose
    a finite lateral-acceleration speed restriction.
    """
    ab = hypot(b.x - a.x, b.y - a.y)
    bc = hypot(c.x - b.x, c.y - b.y)
    ca = hypot(a.x - c.x, a.y - c.y)
    twice_area = abs(
        (b.x - a.x) * (c.y - a.y)
        - (b.y - a.y) * (c.x - a.x)
    )
    if min(ab, bc, ca) <= 0.0 or twice_area <= 1e-12:
        return float("inf")
    return ab * bc * ca / (2.0 * twice_area)


def minimum_curve_radius_m(points: tuple[Point, ...]) -> float:
    """Return the smallest finite curve radius in a polyline."""
    radii = tuple(
        radius
        for a, b, c in zip(points, points[1:], points[2:])
        for radius in (circumradius_m(a, b, c),)
        if isfinite(radius)
    )
    return min(radii, default=float("inf"))


@dataclass(frozen=True, slots=True)
class BoundingBox:
    min_x: float
    min_y: float
    max_x: float
    max_y: float

    def __post_init__(self) -> None:
        if self.min_x > self.max_x or self.min_y > self.max_y:
            raise ValueError("Invalid bounding box")
