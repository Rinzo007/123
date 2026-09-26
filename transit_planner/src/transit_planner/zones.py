from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Iterable

from .city import DemandZone


@dataclass(frozen=True, slots=True)
class ZoneCell:
    id: str
    min_x: float
    min_y: float
    max_x: float
    max_y: float
    population: float = 0.0
    jobs: float = 0.0

    @property
    def centroid(self) -> tuple[float, float]:
        return ((self.min_x + self.max_x) / 2.0, (self.min_y + self.max_y) / 2.0)


def generate_grid_zones(
    min_x: float,
    min_y: float,
    max_x: float,
    max_y: float,
    *,
    cell_size: float,
    population_points: Iterable[tuple[float, float, float]] = (),
    job_points: Iterable[tuple[float, float, float]] = (),
) -> tuple[DemandZone, ...]:
    if cell_size <= 0:
        raise ValueError("cell_size must be positive")
    if min_x >= max_x or min_y >= max_y:
        raise ValueError("Invalid zone extent")

    zones: list[DemandZone] = []
    y = min_y
    row = 0
    while y < max_y:
        x = min_x
        col = 0
        top = min(y + cell_size, max_y)
        while x < max_x:
            right = min(x + cell_size, max_x)
            population = _aggregate_points(population_points, x, y, right, top)
            jobs = _aggregate_points(job_points, x, y, right, top)
            zones.append(
                DemandZone(
                    id=f"z{row:04d}_{col:04d}",
                    centroid_x=(x + right) / 2.0,
                    centroid_y=(y + top) / 2.0,
                    population=population,
                    jobs=jobs,
                )
            )
            x = right
            col += 1
        y = top
        row += 1
    return tuple(zones)


def nearest_zone(
    zones: Iterable[DemandZone], x: float, y: float
) -> DemandZone | None:
    best: DemandZone | None = None
    best_distance = float("inf")
    for zone in zones:
        distance = sqrt((zone.centroid_x - x) ** 2 + (zone.centroid_y - y) ** 2)
        if distance < best_distance:
            best_distance = distance
            best = zone
    return best


def _aggregate_points(
    points: Iterable[tuple[float, float, float]],
    min_x: float,
    min_y: float,
    max_x: float,
    max_y: float,
) -> float:
    total = 0.0
    for x, y, value in points:
        if min_x <= x < max_x and min_y <= y < max_y:
            total += max(0.0, float(value))
    return total
