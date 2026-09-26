from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from math import sqrt


@dataclass(frozen=True, slots=True)
class IndexedPoint:
    id: int
    x: float
    y: float


class GridPointIndex:
    """Dependency-free exact grid index for nearest-point lookup.

    Coordinates are assumed to use the same metric as cell_size. For geographic
    degrees, transform coordinates before inserting them.
    """

    def __init__(self, cell_size: float = 100.0) -> None:
        if cell_size <= 0:
            raise ValueError("cell_size must be positive")
        self.cell_size = cell_size
        self._cells: dict[tuple[int, int], list[IndexedPoint]] = defaultdict(list)
        self._min_cell: tuple[int, int] | None = None
        self._max_cell: tuple[int, int] | None = None

    def _cell(self, x: float, y: float) -> tuple[int, int]:
        return int(x // self.cell_size), int(y // self.cell_size)

    def insert(self, point: IndexedPoint) -> None:
        cell = self._cell(point.x, point.y)
        self._cells[cell].append(point)
        if self._min_cell is None:
            self._min_cell = self._max_cell = cell
        else:
            min_x, min_y = self._min_cell
            max_x, max_y = self._max_cell
            self._min_cell = (min(min_x, cell[0]), min(min_y, cell[1]))
            self._max_cell = (max(max_x, cell[0]), max(max_y, cell[1]))

    def clear(self) -> None:
        self._cells.clear()
        self._min_cell = None
        self._max_cell = None

    def nearest(
        self,
        x: float,
        y: float,
        max_radius: float | None = None,
    ) -> IndexedPoint | None:
        if max_radius is not None and max_radius < 0:
            return None
        if not self._cells:
            return None

        center_x, center_y = self._cell(x, y)
        best: IndexedPoint | None = None
        best_distance = (
            max_radius if max_radius is not None else float("inf")
        )

        if max_radius is not None:
            radius_cells = int(max_radius // self.cell_size) + 1
            ranges = range(-radius_cells, radius_cells + 1)
            for dx in ranges:
                for dy in ranges:
                    self._scan_cell(
                        center_x + dx,
                        center_y + dy,
                        x,
                        y,
                        best_distance,
                    )
                    candidate = self._nearest_in_cell(
                        center_x + dx,
                        center_y + dy,
                        x,
                        y,
                        best_distance,
                    )
                    if candidate is not None:
                        candidate_point, candidate_distance = candidate
                        if candidate_distance <= best_distance:
                            best = candidate_point
                            best_distance = candidate_distance
            return best

        assert self._min_cell is not None and self._max_cell is not None
        max_ring = max(
            abs(self._min_cell[0] - center_x),
            abs(self._min_cell[1] - center_y),
            abs(self._max_cell[0] - center_x),
            abs(self._max_cell[1] - center_y),
        )

        for ring in range(max_ring + 1):
            for dx in range(-ring, ring + 1):
                for dy in range(-ring, ring + 1):
                    if max(abs(dx), abs(dy)) != ring:
                        continue
                    candidate = self._nearest_in_cell(
                        center_x + dx,
                        center_y + dy,
                        x,
                        y,
                        best_distance,
                    )
                    if candidate is not None:
                        candidate_point, candidate_distance = candidate
                        if candidate_distance < best_distance:
                            best = candidate_point
                            best_distance = candidate_distance

            if best is not None and ring < max_ring:
                next_ring = ring + 1
                lower_bound = self._ring_lower_bound(
                    center_x,
                    center_y,
                    next_ring,
                    x,
                    y,
                )
                if lower_bound >= best_distance:
                    break

        return best

    def _nearest_in_cell(
        self,
        cell_x: int,
        cell_y: int,
        x: float,
        y: float,
        limit: float,
    ) -> tuple[IndexedPoint, float] | None:
        best: IndexedPoint | None = None
        best_distance = limit
        for point in self._cells.get((cell_x, cell_y), ()):
            distance = sqrt((point.x - x) ** 2 + (point.y - y) ** 2)
            if distance < best_distance:
                best = point
                best_distance = distance
        return None if best is None else (best, best_distance)

    def _scan_cell(
        self,
        cell_x: int,
        cell_y: int,
        x: float,
        y: float,
        limit: float,
    ) -> None:
        return None

    def _ring_lower_bound(
        self,
        center_x: int,
        center_y: int,
        ring: int,
        x: float,
        y: float,
    ) -> float:
        minimum = float("inf")
        for dx in range(-ring, ring + 1):
            for dy in range(-ring, ring + 1):
                if max(abs(dx), abs(dy)) != ring:
                    continue
                minimum = min(
                    minimum,
                    self._cell_lower_bound(
                        center_x + dx,
                        center_y + dy,
                        x,
                        y,
                    ),
                )
        return minimum

    def _cell_lower_bound(
        self,
        cell_x: int,
        cell_y: int,
        x: float,
        y: float,
    ) -> float:
        minimum_x = cell_x * self.cell_size
        maximum_x = minimum_x + self.cell_size
        minimum_y = cell_y * self.cell_size
        maximum_y = minimum_y + self.cell_size
        dx = max(minimum_x - x, 0.0, x - maximum_x)
        dy = max(minimum_y - y, 0.0, y - maximum_y)
        return sqrt(dx * dx + dy * dy)


