"""Построение дорожной граф-сети из отрезков Overture.

Координаты везде ``(lat, lon)``. Узлы дедуплицируются по округлённым
координатам (5 знаков ≈ 1 м). Каждый отрезок сегмента дороги становится
ребром с весом по гаверсинусу (км).

Используется ``heapq`` (стандартная библиотека) — внешних зависимостей нет.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from .geometry import haversine_km
from .type_defs import Coordinate

__all__ = ["RoadNetwork"]

# Точность дедупликации узлов и метрики для эвристики A*.
_ROUND_DIGITS: int = 5
_NODE_GRID: float = 10 ** (-_ROUND_DIGITS)
_KM_PER_DEG_LAT: float = 111.32
_KM_PER_DEG_LON: float = 111.32

# Размер ячейки пространственного индекса для быстрой привязки узлов
# (~0.002° ≈ 200 м на широте города). Каждая ячейка хранит несколько узлов.
_SNAP_CELL: float = 0.002
# Предел расширения сетки при поиске ближайшего узла (~6.5 км).
_SNAP_MAX_RADIUS: int = 32


def _snap_key(lat: float, lon: float) -> tuple[int, int]:
    """Возвращает целочисленный ключ сетки для дедупликации узлов."""
    return round(lat / _NODE_GRID), round(lon / _NODE_GRID)


class RoadNetwork:
    """Неориентированный граф дорожной сети из LineString-отрезков.

    Каждый уникальный ``(lat, lon)`` (после округления) — узел; каждый
    отрезок ``(lat1, lon1) → (lat2, lon2)`` — ребро с весом ``haversine_km``.
    """

    __slots__ = ("_adj", "_coords", "_next_id", "_snap_idx")

    def __init__(self) -> None:
        # node_id → [(neighbor_id, weight_km)]
        self._adj: dict[int, list[tuple[int, float]]] = {}
        # node_id → (lat, lon)
        self._coords: dict[int, Coordinate] = {}
        # snap_key → node_id
        self._snap_idx: dict[tuple[int, int], int] = {}
        # (cell_x, cell_y) → [node_id, ...] для пространственной привязки
        self._cells: dict[tuple[int, int], list[int]] = {}
        # node_id → id компоненты связности (лениво, после сборки графа)
        self._comp: dict[int, int] | None = None
        self._next_id: int = 0

    # ------------------------------------------------------------------
    # Построение графа
    # ------------------------------------------------------------------

    def _get_or_create_node(self, lat: float, lon: float) -> int:
        key = _snap_key(lat, lon)
        node_id = self._snap_idx.get(key)
        if node_id is not None:
            return node_id
        node_id = self._next_id
        self._next_id += 1
        self._snap_idx[key] = node_id
        self._coords[node_id] = (lat, lon)
        self._adj.setdefault(node_id, [])
        cell = (math.floor(lat / _SNAP_CELL), math.floor(lon / _SNAP_CELL))
        self._cells.setdefault(cell, []).append(node_id)
        return node_id

    def add_segment(self, lat1: float, lon1: float, lat2: float, lon2: float) -> None:
        """Добавляет неориентированное ребро (отрезок дороги).

        Нулевые и дублирующиеся рёбра пропускаются.
        """
        w = haversine_km(lat1, lon1, lat2, lon2)
        if w <= 0.0:
            return
        u = self._get_or_create_node(lat1, lon1)
        v = self._get_or_create_node(lat2, lon2)
        if u == v:
            return
        if any(neighbor == v for neighbor, _weight in self._adj[u]):
            return
        self._adj[u].append((v, w))
        self._adj[v].append((u, w))

    def add_linestring(self, coords: Sequence[Coordinate]) -> None:
        """Добавляет все отрезки полилинии ``(lat, lon)``."""
        for i in range(len(coords) - 1):
            lat1, lon1 = coords[i]
            lat2, lon2 = coords[i + 1]
            self.add_segment(lat1, lon1, lat2, lon2)

    def add_geometry(self, geom: Any) -> None:
        """Добавляет ``shapely.geometry.LineString`` / ``MultiLineString``."""
        if geom is None:
            return
        geom_type = getattr(geom, "geom_type", "")
        if geom_type == "MultiLineString":
            for part in geom.geoms:
                self.add_geometry(part)
            return
        if geom_type == "LineString":
            coords = [(c[1], c[0]) for c in getattr(geom, "coords", ())]  # (lon, lat) → (lat, lon)
            self.add_linestring(coords)
