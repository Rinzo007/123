"""Дорожная сеть для OD: способы извлечения дорог и матрицы затрат.

Overpass/Overture способы приводятся к единому формату ``{highway, coords}``,
граф строит рёбра по времени ``расстояние/скорость``; матрицы кратчайшего
времени считаются как по сети, так и по прямой (fallback).
"""

from __future__ import annotations

import itertools
import tempfile
from pathlib import Path
from typing import Any

import networkx as nx
import numpy as np
from scipy.spatial import cKDTree

from ...cache import JsonCache
from ...type_defs import Coordinate
from ..model import (
    OdMatrixError,
    Zones,
    _cosscale,
    haversine_km,
)

__all__ = [
    "build_road_graph",
    "euclidean_costs",
    "fetch_road_ways",
    "ways_from_overpass",
    "ways_from_overture",
    "zone_network_costs",
]

_ROAD_SPEED_KMH = {
    "motorway": 90.0,
    "motorway_link": 50.0,
    "trunk": 70.0,
    "trunk_link": 40.0,
    "primary": 60.0,
    "primary_link": 35.0,
    "secondary": 50.0,
    "secondary_link": 30.0,
    "tertiary": 40.0,
    "tertiary_link": 25.0,
    "unclassified": 30.0,
    "residential": 30.0,
    "living_street": 20.0,
    "service": 20.0,
    "road": 30.0,
    "track": 15.0,
    "pedestrian": 5.0,
    "footway": 5.0,
    "cycleway": 15.0,
    "services": 20.0,
}

_OD_ROAD_CLASSES: frozenset[str] = frozenset(_ROAD_SPEED_KMH)
_FALLBACK_SPEED_KMH = 30.0
_OD_CACHE_KIND = "od_roads"

# Округление узлов графа: ~0.1 м по широте. Позволяет склеивать координаты
# пересечений, различающиеся лишь хвостом float, не сливая соседние полосы.
_NODE_SNAP_DECIMALS = 6


def _way_speed(highway: str | None) -> float:
    return _ROAD_SPEED_KMH.get(str(highway or "").lower(), _FALLBACK_SPEED_KMH)


def _snap_coord(coord: tuple[float, float]) -> tuple[float, float]:
    return (round(coord[0], _NODE_SNAP_DECIMALS), round(coord[1], _NODE_SNAP_DECIMALS))


def _way_coords(geometry: Any) -> list[tuple[float, float]]:
    coords: list[tuple[float, float]] = []
    for point in geometry or []:
        coord = (float(point["lon"]), float(point["lat"]))
        if not coords or coords[-1] != coord:
            coords.append(coord)
    return coords


def ways_from_overpass(data: Any) -> list[dict[str, Any]]:
    """Извлекает дороги ``way`` (highway, coords) из ответа Overpass ``out geom``."""
    elements = data.get("elements", []) if isinstance(data, dict) else []
    ways: list[dict[str, Any]] = []
    for element in elements:
        if element.get("type") != "way":
            continue
        coords = _way_coords(element.get("geometry"))
        if len(coords) < 2:
            continue
        tags = element.get("tags") or {}
        ways.append({"highway": tags.get("highway"), "coords": tuple(coords)})
    return ways


class _ClassFallback:
    """Возвращает класс дороги из gdf-строки Overture с fallback на default."""

    def __init__(self, default: str) -> None:
        self._default = default

    def get(self, class_value: Any) -> str:
        return str(class_value) if class_value else self._default


def ways_from_overture(gdf: Any) -> list[dict[str, Any]]:
    """Преобразует GeoDataFrame Overture (segment) в формат ``{highway, coords}``.

    Координаты из ``LineString``/``MultiLineString`` в EPSG:4326, класс дороги
    из колонки ``class`` (fallback — ``road``). Каждая часть MultiLineString
    становится отдельной дорогой: склейка частей создала бы фиктивные рёбра
    между несмежными сегментами.
    """
    if gdf is None or len(gdf) == 0:
        return []
    fallback = _ClassFallback("road")
    class_col = "class" if "class" in gdf.columns else None
    ways: list[dict[str, Any]] = []
    for _, row in gdf.iterrows():
        geometry = row.geometry
        if geometry is None or geometry.is_empty:
            continue
        class_value = None
        if class_col is not None:
            class_value = row.get(class_col, fallback)
        cls = fallback.get(class_value)
        if geometry.geom_type == "LineString":
            parts = [geometry]
        elif geometry.geom_type == "MultiLineString":
            parts = list(geometry.geoms)
        else:
            continue
        for part in parts:
            coords: list[tuple[float, float]] = [
                (float(c[0]), float(c[1])) for c in part.coords
            ]
            merged: list[tuple[float, float]] = []
            for coord in coords:
                if not merged or merged[-1] != coord:
                    merged.append(coord)
            if len(merged) < 2:
                continue
            ways.append({"highway": cls, "coords": tuple(merged)})
    return ways


def build_road_graph(ways: list[dict[str, Any]]) -> nx.Graph:
    """Строит граф дорог: рёбра по времени ``расстояние/скорость``.

    Узлы округляются до ``_NODE_SNAP_DECIMALS`` знаков (≈0.1 м по широте),
    чтобы пересечения дорог с координатами, отличающимися на длинный хвост
    плавающей точки, корректно схлопывались в один узел.
    """
    graph = nx.Graph()
    for way in ways:
        speed = _way_speed(way.get("highway"))
        raw = way.get("coords") or ()
        coords = [_snap_coord(coord) for coord in raw]
        for a, b in itertools.pairwise(coords):
            minutes = haversine_km(a, b) / speed * 60.0
            if graph.has_edge(a, b):
                graph[a][b]["time"] = min(graph[a][b]["time"], minutes)
            else:
                graph.add_edge(a, b, time=minutes)
    return graph


def _snapped_centroids(zones: Zones, graph: nx.Graph) -> list[Coordinate]:
    nodes = list(graph.nodes)
    array = np.asarray(nodes, dtype=float)
    scale = _cosscale(array, zones.xy)
    tree = cKDTree(array * [scale, 1.0])
    _, nearest = tree.query(zones.xy * [scale, 1.0], k=1)
    return [nodes[int(index)] for index in nearest]


def zone_network_costs(zones: Zones, graph: nx.Graph) -> np.ndarray:
    """Матрица кратчайшего времени по сети между зонами (минуты)."""
    nodes = list(graph.nodes)
    if not nodes:
        raise OdMatrixError("Дорожный граф пуст")
    snapped = _snapped_centroids(zones, graph)
    n = len(zones)
    cost = np.full((n, n), np.inf, dtype=np.float32)
    np.fill_diagonal(cost, 0.0)
    # Зоны с одинаковой привязкой к узлу сети считают один общий Dijkstra.
    grouped: dict[Coordinate, list[int]] = {}
    for i, source in enumerate(snapped):
        grouped.setdefault(source, []).append(i)
    for source, indices in grouped.items():
        lengths = nx.single_source_dijkstra_path_length(graph, source, weight="time")
        for i in indices:
            for j, target in enumerate(snapped):
                if i == j:
                    continue
                value = lengths.get(target)
                if value is not None:
                    cost[i, j] = float(value)
    return cost


def euclidean_costs(
    zones: Zones, *, speed_kmh: float = _FALLBACK_SPEED_KMH
) -> np.ndarray:
    """Евклидово время по прямой, минуты (fallback без дорожной сети).

    Векторизованная версия: матрица расстояний вычисляется сразу по всем
    парам зон (без вложенного Python-цикла, т.е. быстрее на больших сетках).
    """
    lon = np.deg2rad(zones.xy[:, 0])
    lat = np.deg2rad(zones.xy[:, 1])
    dlat = lat[:, None] - lat[None, :]
    dlon = lon[:, None] - lon[None, :]
    h = (
        np.sin(dlat / 2.0) ** 2
        + np.cos(lat)[:, None] * np.cos(lat)[None, :] * np.sin(dlon / 2.0) ** 2
    )
    dist_km = 6371.0 * 2.0 * np.arcsin(np.sqrt(np.minimum(h, 1.0)))
    np.fill_diagonal(dist_km, 0.0)
    return (dist_km / speed_kmh * 60.0).astype(np.float32)


def fetch_road_ways(
    boundary: Any,
    session: Any,
    cache: JsonCache,
    *,
    cache_key: str,
    refresh: bool = False,
    config: Any = None,
) -> list[dict[str, Any]] | None:
    """Загружает автодороги в bbox границы через Overture (с кэшем)."""
    if cache:
        cached = cache.get(_OD_CACHE_KIND, cache_key)
        if isinstance(cached, list) and not refresh:
            return cached
    from ...overture.load import load_overture_segments

    minx, miny, maxx, maxy = boundary.bounds
    bbox = (miny, minx, maxy, maxx)  # (min_lat, min_lon, max_lat, max_lon)
    release = getattr(config, "overture_release", None) if config else None
    retries = getattr(config, "overture_download_retries", 0) if config else 0
    cache_dir = cache.root if cache else Path(tempfile.mkdtemp(prefix="od_overture_"))
    gdf = load_overture_segments(
        bbox,
        release,
        str(cache_dir),
        retries=retries,
        classes=_OD_ROAD_CLASSES,
    )
    ways = ways_from_overture(gdf)
    if ways:
        if cache:
            cache.put(_OD_CACHE_KIND, cache_key, ways)
        return ways
    return None
