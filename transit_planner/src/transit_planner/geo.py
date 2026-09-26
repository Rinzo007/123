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


@dataclass(frozen=True, slots=True)
class BoundingBox:
    min_x: float
    min_y: float
    max_x: float
    max_y: float

    def __post_init__(self) -> None:
        if self.min_x > self.max_x or self.min_y > self.max_y:
            raise ValueError("Invalid bounding box")
