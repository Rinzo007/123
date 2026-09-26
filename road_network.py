"""Построение дорожной граф-сети из отрезков Overture.

Координаты везде ``(lat, lon)``. Узлы дедуплицируются по округлённым
координатам (5 знаков ≈ 1 м). Каждый отрезок сегмента дороги становится
ребром с весом по гаверсину (км).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .geometry import haversine_km
from .type_defs import Coordinate

__all__ = ["RoadNetwork"]

# Точность дедупликации узлов.
_ROUND_DIGITS: int = 5
_NODE_GRID: float = 10 ** (-_ROUND_DIGITS)


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
        self._adj: dict[int, list[tuple[int, float]]] = {}
        self._coords: dict[int, Coordinate] = {}
        self._snap_idx: dict[tuple[int, int], int] = {}
        self._next_id: int = 0

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
            coords = [(c[1], c[0]) for c in getattr(geom, "coords", ())]
            self.add_linestring(coords)
