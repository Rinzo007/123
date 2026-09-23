"""Построение дорожной граф-сети из отрезков Overture и кратчайший путь (A*).

Координаты везде ``(lat, lon)``. Узлы дедуплицируются по округлённым
координатам (5 знаков ≈ 1 м). Каждый отрезок сегмента дороги становится
ребром с весом по гаверсинусу (км).

Используется ``heapq`` (стандартная библиотека) — внешних зависимостей нет.
"""

from __future__ import annotations

import heapq
import math
from collections.abc import Iterator, Sequence
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


def _cell_coords(lat: float, lon: float, radius_cells: int) -> list[tuple[int, int]]:
    """Список ключей сеток вокруг точки для поиска ближайших узлов."""
    cx, cy = math.floor(lat / _SNAP_CELL), math.floor(lon / _SNAP_CELL)
    return [
        (cx + dx, cy + dy)
        for dx in range(-radius_cells, radius_cells + 1)
        for dy in range(-radius_cells, radius_cells + 1)
    ]


class RoadNetwork:
    """Неориентированный граф дорожной сети из LineString-отрезков.

    Каждый уникальный ``(lat, lon)`` (после округления) — узел; каждый
    отрезок ``(lat1, lon1) → (lat2, lon2)`` — ребро с весом ``haversine_km``.
    """

    __slots__ = ("_adj", "_cells", "_comp", "_coords", "_next_id", "_snap_idx")

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

    # ------------------------------------------------------------------
    # Метрика
    # ------------------------------------------------------------------

    @property
    def node_count(self) -> int:
        return len(self._coords)

    @property
    def edge_count(self) -> int:
        return sum(len(neighbors) for neighbors in self._adj.values()) // 2

    def iter_edges(self) -> Iterator[tuple[int, int, float]]:
        """Итератор рёбер ``(u, v, weight_km)`` без дублей (однажды на пару)."""
        seen: set[tuple[int, int]] = set()
        for u, neighbors in self._adj.items():
            for v, w in neighbors:
                key = (u, v) if u <= v else (v, u)
                if key in seen:
                    continue
                seen.add(key)
                yield key[0], key[1], w

    # ------------------------------------------------------------------
    # Связность
    # ------------------------------------------------------------------

    def _fill_component(self, start: int, comp: dict[int, int], comp_id: int) -> None:
        """Размечает одну компоненту связности (обход в глубину)."""
        stack: list[int] = [start]
        comp[start] = comp_id
        while stack:
            u = stack.pop()
            for v, _w in self._adj.get(u, ()):
                if v in comp:
                    continue
                comp[v] = comp_id
                stack.append(v)

    def _ensure_components(self) -> None:
        """Размечает компоненты связности один раз (обход по всему графу)."""
        if self._comp is not None:
            return
        comp: dict[int, int] = {}
        comp_id = 0
        for start in self._adj:
            if start in comp:
                continue
            self._fill_component(start, comp, comp_id)
            comp_id += 1
        self._comp = comp

    def same_component(self, a: int, b: int) -> bool:
        """True, если узлы в одной компоненте связности (с минимумом разметки)."""
        if a not in self._coords or b not in self._coords:
            return False
        self._ensure_components()
        return self._comp.get(a) == self._comp.get(b)

    # ------------------------------------------------------------------
    # Snap: ближайший узел к точке
    # ------------------------------------------------------------------

    def _nearest_node_in_cells(
        self, lat: float, lon: float, cells: Sequence[tuple[int, int]]
    ) -> int | None:
        """Ближайший к точке узел среди данных ячеек сетки или ``None``."""
        best_id: int = -1
        best_dist: float = math.inf
        for cell in cells:
            for nid in self._cells.get(cell, ()):
                nlat, nlon = self._coords[nid]
                d = haversine_km(lat, lon, nlat, nlon)
                if d < best_dist:
                    best_dist = d
                    best_id = nid
        return best_id if best_id >= 0 else None

    def snap_point(self, lat: float, lon: float) -> int | None:
        """Индекс ближайшего узла или ``None``, если граф пуст."""
        if not self._snap_idx:
            return None
        node_id = self._snap_idx.get(_snap_key(lat, lon))
        if node_id is not None:
            return node_id
        # Пространственный поиск: расширяем радиус по сетке, пока не найдём узел.
        # Предел в 32 ячейки (~6.5 км) покрывает любую точку города.
        for radius in range(1, _SNAP_MAX_RADIUS + 1):
            found = self._nearest_node_in_cells(
                lat, lon, _cell_coords(lat, lon, radius)
            )
            if found is not None:
                return found
        return None

    # ------------------------------------------------------------------
    # Кратчайший путь: Dijkstra (приоритетная очередь)
    # ------------------------------------------------------------------

    def _heuristic(
        self, node_id: int, tlat: float, tlon: float, cos_lat: float
    ) -> float:
        """Эвристика A*: эквиперректangularное расстояние до цели (км/град).

        Значительно дешевле гаверсинуса: один ``sqrt`` на вызов. Коэффициент
        ``cos_lat`` (косинус средней широты) вычисляется один раз на запрос.
        Оценка не завышает истинное кратчайшее расстояние (дороги не короче
        прямой), поэтому A* остаётся точным.
        """
        nlat, nlon = self._coords[node_id]
        dlat = (nlat - tlat) * _KM_PER_DEG_LAT
        dlon = (nlon - tlon) * _KM_PER_DEG_LON * cos_lat
        return math.sqrt(dlat * dlat + dlon * dlon)

    def shortest_path(self, start: int, target: int) -> list[Coordinate] | None:
        """Кратчайший путь ``start → target`` (A*); список ``(lat, lon)`` или None.

        Эквиперректangularная оценка расстояния до цели — допустимая эвристика
        для весов по гаверсинусу, поэтому A* даёт точный кратчайший путь,
        исследуя существенно меньшую область графа, чем полный Dijkstra.
        """
        if start not in self._coords or target not in self._coords:
            return None
        if start == target:
            return [self._coords[start]]

        tlat, tlon = self._coords[target]
        cos_lat = math.cos(math.radians((self._coords[start][0] + tlat) / 2.0))
        g_score: dict[int, float] = {start: 0.0}
        prev: dict[int, int] = {}
        heap: list[tuple[float, int]] = [
            (self._heuristic(start, tlat, tlon, cos_lat), start)
        ]

        while heap:
            _f, u = heapq.heappop(heap)
            if u == target:
                break
            g_u = g_score[u]
            for v, w in self._adj.get(u, ()):
                tentative = g_u + w
                if tentative < g_score.get(v, math.inf):
                    g_score[v] = tentative
                    prev[v] = u
                    f = tentative + self._heuristic(v, tlat, tlon, cos_lat)
                    heapq.heappush(heap, (f, v))
        else:
            return None  # target недостижим

        path_ids: list[int] = []
        cur: int | None = target
        while cur is not None:
            path_ids.append(cur)
            cur = prev.get(cur)
        path_ids.reverse()
        return [self._coords[nid] for nid in path_ids]

    # ------------------------------------------------------------------
    # Вспомогательные методы для тестирования и отладки
    # ------------------------------------------------------------------

    def node_coord(self, node_id: int) -> Coordinate | None:
        return self._coords.get(node_id)
