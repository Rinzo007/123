"""Дорожная сеть для OD: способы извлечения дорог и матрицы затрат.

Overpass/Overture способы приводятся к единому формату ``{highway, coords}``,
граф строит рёбра по времени ``расстояние/скорость``; матрицы кратчайшего
времени считаются как по сети, так и по прямой (fallback).
"""

from __future__ import annotations

import itertools
import os
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import networkx as nx
import numpy as np
from scipy import sparse
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree

from cache import JsonCache
from type_defs import Coordinate
from ..model import (
    OdMatrixError,
    Zones,
    _cosscale,
    haversine_km,
)

__all__ = [
    "OD_ROADS_CACHE_ENTRY_BYTES",
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

# Кэш дорог города — набор way-геометрий; у крупных городов он не влезает
# в общий лимит записи JsonCache (8 МБ), поэтому для kind задан свой потолок.
OD_ROADS_CACHE_ENTRY_BYTES = 512 * 1024 * 1024

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


def ways_from_overture(gdf: Any) -> list[dict[str, Any]]:
    """Преобразует GeoDataFrame Overture (segment) в формат ``{highway, coords}``.

    Координаты из ``LineString``/``MultiLineString`` в EPSG:4326, класс дороги
    из колонки ``class`` (fallback — ``road``). Каждая часть MultiLineString
    становится отдельной дорогой: склейка частей создала бы фиктивные рёбра
    между несмежными сегментами.
    """
    if gdf is None or len(gdf) == 0:
        return []
    class_col = "class" if "class" in gdf.columns else None
    ways: list[dict[str, Any]] = []
    # Прямой обход геометрий без iterrows: для больших городов (десятки тысяч
    # сегментов) разница в десятки раз. Координаты вынимаются за раз через
    # numpy, соседние дубли отбрасываются векторно.
    geometry = gdf.geometry
    for i, geom in enumerate(geometry):
        if geom is None or geom.is_empty:
            continue
        cls = "road"
        if class_col is not None:
            value = gdf[class_col].iat[i]
            cls = str(value) if value else "road"
        if geom.geom_type == "LineString":
            parts: tuple[Any, ...] = (geom,)
        elif geom.geom_type == "MultiLineString":
            parts = list(geom.geoms)
        else:
            continue
        for part in parts:
            arr = np.array(list(part.coords), dtype=np.float64)
            if arr.ndim != 2 or arr.shape[1] != 2 or len(arr) < 2:
                continue
            keep = np.concatenate(
                ([True], np.any(arr[1:] != arr[:-1], axis=1))
            )
            deduped = arr[keep]
            if len(deduped) < 2:
                continue
            ways.append(
                {"highway": cls, "coords": tuple(map(tuple, deduped))}
            )
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


# Батчами, чтобы расстояние (батч × V) не раздувало память на граф города.
_DIJKSTRA_BATCH_CELLS = 8_000_000
# Параллельные батчи Дейкстры (процессы) включаются только на больших
# задачах: спавн процессов на Windows дорог, мелким графам он не нужен.
_DIJKSTRA_PARALLEL_MIN_SOURCES = 400


def _dijkstra_workers(n_spans: int) -> int:
    """Число процессов для батчей Дейкстры (1 — последовательно).

    Переменная окружения ``WIKIROUTES_OD_DIJKSTRA_WORKERS``: 0/пусто — авто
    (по числу батчей и CPU), 1 — принудительно последовательно, N — не
    более N процессов (аварийный выход и экономия памяти).
    """
    try:
        forced = int(os.environ.get("WIKIROUTES_OD_DIJKSTRA_WORKERS", "0") or 0)
    except ValueError:
        forced = 0
    if forced == 1:
        return 1
    if forced > 1:
        return min(n_spans, forced)
    return min(n_spans, os.cpu_count() or 1)


def _contract_degree_two(
    neighbours: dict[Any, dict[Any, float]], protected: set[Any]
) -> None:
    """Стягивает цепочки узлов степени 2, суммируя время рёбер (in-place).

    Узлы из ``protected`` (привязки зон) не трогаются. Кратчайшие времена
    между оставшимися узлами не меняются: contraction точна для Дейкстры.
    """
    from collections import deque

    queue = deque(
        node
        for node, nbrs in neighbours.items()
        if len(nbrs) == 2 and node not in protected
    )
    queued = set(queue)
    while queue:
        node = queue.popleft()
        queued.discard(node)
        nbrs = neighbours.get(node)
        if nbrs is None or len(nbrs) != 2 or node in protected:
            continue
        (left, left_w), (right, right_w) = list(nbrs.items())
        if left == node or right == node:
            # Петля: стягивание некорректно — оставляем узел как есть
            # (на кратчайшие пути петли не влияют).
            continue
        merged = left_w + right_w
        neighbours[left].pop(node, None)
        neighbours[right].pop(node, None)
        if right not in neighbours[left] or merged < neighbours[left][right]:
            neighbours[left][right] = merged
            neighbours[right][left] = merged
        del neighbours[node]
        for other in (left, right):
            if (
                len(neighbours[other]) == 2
                and other not in protected
                and other not in queued
            ):
                queue.append(other)
                queued.add(other)


def _dijkstra_worker(payload: tuple[Any, np.ndarray]) -> np.ndarray:
    """Один батч Дейкстры в отдельном процессе (picklable для spawn)."""
    adj, indices = payload
    return np.asarray(dijkstra(adj, directed=False, indices=indices))


def zone_network_costs(zones: Zones, graph: nx.Graph) -> np.ndarray:
    """Матрица кратчайшего времени по сети между зонами (минуты).

    Кратчайшие пути считаются одним векторизованным Дейкстрой scipy по всем
    уникальным узлам привязки (батчами, чтобы матрица расстояний не росла),
    а не по одному чистому Python-Дейкстре networkx на узел.
    """
    nodes = list(graph.nodes)
    if not nodes:
        raise OdMatrixError("Дорожный граф пуст")
    snapped = _snapped_centroids(zones, graph)
    n = len(zones)
    cost = np.full((n, n), np.inf, dtype=np.float32)
    np.fill_diagonal(cost, 0.0)

    neighbours: dict[Any, dict[Any, float]] = {node: {} for node in nodes}
    for u, v, time_w in graph.edges(data="time"):
        try:
            weight = float(time_w)
        except (TypeError, ValueError):
            continue
        if not np.isfinite(weight):
            continue
        known = neighbours[u].get(v)
        if known is None or weight < known:
            neighbours[u][v] = weight
            neighbours[v][u] = weight
    # Стягиваем промежуточные точки ways (степень 2): граф меньше —
    # Дейкстра быстрее, времена между привязками зон те же.
    _contract_degree_two(neighbours, set(snapped))
    small_nodes = [node for node in nodes if node in neighbours]
    index = {node: i for i, node in enumerate(small_nodes)}
    rows: list[int] = []
    cols: list[int] = []
    vals: list[float] = []
    for node, nbrs in neighbours.items():
        iu = index[node]
        for other, weight in nbrs.items():
            rows.append(iu)
            cols.append(index[other])
            vals.append(weight)
    adj = sparse.coo_matrix(
        (vals, (rows, cols)),
        shape=(len(small_nodes), len(small_nodes)),
        dtype=np.float64,
    ).tocsr()

    snap_idx = np.fromiter((index[node] for node in snapped), dtype=np.intp, count=n)
    sources, inverse = np.unique(snap_idx, return_inverse=True)
    groups: dict[int, list[int]] = {}
    for i, pos in enumerate(inverse):
        groups.setdefault(int(pos), []).append(i)
    batch = max(1, _DIJKSTRA_BATCH_CELLS // max(len(small_nodes), 1))
    spans = [
        (start, min(start + batch, len(sources)))
        for start in range(0, len(sources), batch)
    ]
    if len(sources) >= _DIJKSTRA_PARALLEL_MIN_SOURCES:
        workers = _dijkstra_workers(len(spans))
    else:
        workers = 1
    if workers > 1 and len(spans) > 1:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            dists = list(
                pool.map(
                    _dijkstra_worker,
                    [(adj, sources[start:stop]) for start, stop in spans],
                )
            )
    else:
        dists = [
            np.asarray(dijkstra(adj, directed=False, indices=sources[start:stop]))
            for start, stop in spans
        ]
    for (start, _), dist in zip(spans, dists):
        for local in range(dist.shape[0]):
            cost[groups[start + local], :] = dist[local, snap_idx]
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
