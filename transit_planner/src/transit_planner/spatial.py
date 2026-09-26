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
    """Small dependency-free spatial index for point lookup.

    Coordinates are assumed to use the same metric as cell_size. For geographic
    degrees, transform coordinates before inserting them.
    """

    def __init__(self, cell_size: float = 100.0) -> None:
        if cell_size <= 0:
            raise ValueError("cell_size must be positive")
        self.cell_size = cell_size
        self._cells: dict[tuple[int, int], list[IndexedPoint]] = defaultdict(list)

    def _cell(self, x: float, y: float) -> tuple[int, int]:
        return int(x // self.cell_size), int(y // self.cell_size)

    def insert(self, point: IndexedPoint) -> None:
        self._cells[self._cell(point.x, point.y)].append(point)

    def clear(self) -> None:
        self._cells.clear()

    def nearest(self, x: float, y: float, max_radius: float | None = None) -> IndexedPoint | None:
        best: IndexedPoint | None = None
        best_distance = max_radius if max_radius is not None else float("inf")
        if best_distance is not None and best_distance < 0:
            return None

        center_x, center_y = self._cell(x, y)
        radius_cells = (
            int(best_distance // self.cell_size) + 1
            if best_distance != float("inf")
            else 1024
        )
        for cx in range(center_x - radius_cells, center_x + radius_cells + 1):
            for cy in range(center_y - radius_cells, center_y + radius_cells + 1):
                for point in self._cells.get((cx, cy), ()):
                    distance = sqrt((point.x - x) ** 2 + (point.y - y) ** 2)
                    if distance < best_distance:
                        best_distance = distance
                        best = point
        return best
