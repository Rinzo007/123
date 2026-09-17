"""Расчёт матрицы корреспонденций (OD) по транспортным зонам города.

Зоны — сетка ячеек внутри границы города. Вес зоны — сумма значений
GHS-растра внутри ячейки (или равный 1.0, если растр не задан).
Сопротивление — кратчайшее время по дорожной сети Overture (тема
transportation/segment; при отсутствии данных — евклидово время).
Распределение: гравитация ``exp(-beta*t)`` с балансировкой Фёрнесс.
"""

from __future__ import annotations

import contextlib
import itertools
import json
import math
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import networkx as nx
import numpy as np
from scipy import sparse
from scipy.spatial import cKDTree
from shapely.geometry import Point, box
from shapely.geometry import mapping as shapely_mapping
from shapely.ops import transform as geom_transform

from .cache import JsonCache
from .common import open_raster_index, raster_stack, resolve_sources
from .passenger_flow.models import PURPOSE_DEFAULTS, Period, Purpose
from .passenger_flow.takt import (
    _TAKT_M_PER_DEG_LAT,
    _TAKT_M_PER_DEG_LON_EQUATOR,
)
from .type_defs import Coordinate

_DEFAULT_ZONE_SIZE_M = 750.0
_MIN_CLIP_AREA_RATIO = 0.3
_DECAY_RADIUS_KM = 5.5
_GRAVITY_REF_SPEED_KMH = 25.0
_SPARSE_GRAVITY_CELLS = 3_000_000
_GRAVITY_KERNEL_EPS = 1e-4
_KILOMETERS_PER_DEGREE = 111.32
_FURNESS_MAX_ITER = 300
_FURNESS_TOL = 1e-5
_OD_CACHE_KIND = "od_roads"
_FALLBACK_SPEED_KMH = 30.0
_GEO_PAD_DEG = 0.001
_PROJECTED_PAD_M = 50.0

_TAKT_OD_GRID_LON_DEG = 0.01
_TAKT_OD_GRID_LAT_RATIO = 0.62
_TAKT_OD_POP_CUTOFF = 40.0
_TAKT_OD_MIN_DIST_M = 50.0
_TAKT_OD_CIRCUITY = 1.35
_TAKT_OD_SPEED_MPS = 7.5
_TAKT_OD_BASE_SECONDS = 240.0

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

__all__ = [
    "OdMatrixError",
    "OdResult",
    "PurposeOd",
    "TaktDemand",
    "Zones",
    "assign_district_names",
    "build_gravity_od",
    "build_purpose_od",
    "build_road_graph",
    "build_takt_od",
    "build_zones",
    "euclidean_costs",
    "fetch_road_ways",
    "load_demand_streets",
    "load_districts",
    "load_od_from_files",
    "load_takt_demand",
    "load_zones_from_file",
    "periods_from_purpose_blend",
    "run_od_stage",
    "save_od_outputs",
    "ways_from_overpass",
    "ways_from_overture",
    "zonal_weights",
    "zone_network_costs",
    "zone_weights_from_streets",
]


class OdMatrixError(RuntimeError):
    """Ошибка вычисления матрицы корреспонденций."""


@dataclass(frozen=True, slots=True)
class Zones:
    """Сетка транспортных зон: id, полигоны, центроиды и общий bbox."""

    ids: np.ndarray
    polygons: tuple[Any, ...]
    xy: np.ndarray
    bounds: tuple[float, float, float, float]

    def __len__(self) -> int:
        return int(self.ids.shape[0])


@dataclass(frozen=True, slots=True)
class OdResult:
    """Результат расчёта матрицы корреспонденций."""

    zones: Zones
    matrix: np.ndarray
    costs: np.ndarray
    weights: np.ndarray
    road_ways: int
    euclidean: bool
    road_loads: list[tuple[Any, Any, float]] | None = None
    sparse_matrix: sparse.csr_matrix | None = None
    purpose_matrices: tuple[np.ndarray, ...] | None = None
    purpose_periods: tuple[Period, ...] = ()
    purpose_trips: tuple[float, ...] = ()
    purpose_keys: tuple[str, ...] = ()
    district_names: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PurposeOd:
    """Итог генерации OD по целям поездок (Takt-подобное тяготение).

    ``matrix`` — суммарная матрица, ``purpose_matrices`` — по целям в том же
    порядке, что ``purposes``; ``period_out``/``period_ret`` — взвешенные по
    долям поездок профили периодов суток; ``shares`` — доля каждой цели.
    """

    matrix: np.ndarray
    purpose_matrices: tuple[np.ndarray, ...]
    period_out: tuple[float, ...]
    period_ret: tuple[float, ...]
    shares: tuple[float, ...]
    purposes: tuple[Purpose, ...]


def _as_sparse(matrix: np.ndarray) -> sparse.csr_matrix:
    """Возвращает CSR-представление матрицы OD (только ненулевые пары)."""
    return sparse.csr_matrix(matrix, dtype=np.float64)


@dataclass(frozen=True, slots=True)
class TaktDemand:
    """Готовый спрос из пакета Takt: точки спроса и матрица OD.

    ``points`` — таблица ``[lon, lat, pop, jobs]``; ``production`` — население
    точек; ``matrix`` — поездки между точками из раздела ``od``.
    """

    zones: Zones
    matrix: np.ndarray
    production: np.ndarray
    points: np.ndarray


_DEMAND_CELL_M = 261.0
_DEMAND_RADIUS_DEG = _DEMAND_CELL_M / 2.0 / (_KILOMETERS_PER_DEGREE * 1000.0)


def load_takt_demand(path: str | Path) -> TaktDemand:
    """Читает ``demand.json`` из пакета Takt (точки + OD-пары).

    Зоны строятся как ячейки вокруг каждой точки спроса, production — их
    население, матрица — из пар ``[fromPt, toPt, trips, travelTimeS]``
    (время игнорируется, индексы точек нулевые).
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "pts" not in data or "od" not in data:
        raise OdMatrixError(f"Файл спроса Takt не содержит pts/od: {path}")
    points = np.asarray(data["pts"], dtype=np.float64)
    pairs = np.asarray(data["od"], dtype=np.int64)
    if points.ndim != 2 or points.shape[1] < 3:
        raise OdMatrixError(f"Файл спроса Takt: неверный формат pts: {path}")
    if pairs.ndim != 2 or pairs.shape[1] < 3:
        raise OdMatrixError(f"Файл спроса Takt: неверный формат od: {path}")
    n = points.shape[0]
    if pairs[:, :2].min() < 0 or pairs[:, :2].max() >= n:
        raise OdMatrixError(f"Файл спроса Takt: индекс точки вне диапазона: {path}")
    mean_lat = float(np.mean(points[:, 1]))
    r_lon = _DEMAND_RADIUS_DEG / max(math.cos(math.radians(mean_lat)), 0.2)
    r_lat = _DEMAND_RADIUS_DEG
    polys = tuple(
        box(
            float(p[0]) - r_lon,
            float(p[1]) - r_lat,
            float(p[0]) + r_lon,
            float(p[1]) + r_lat,
        )
        for p in points
    )
    zones = Zones(
        ids=np.arange(1, n + 1, dtype=np.int64),
        polygons=polys,
        xy=points[:, :2].copy(),
        bounds=(
            float(points[:, 0].min()),
            float(points[:, 1].min()),
            float(points[:, 0].max()),
            float(points[:, 1].max()),
        ),
    )
    production = points[:, 2]
    matrix = np.zeros((n, n), dtype=np.float64)
    np.add.at(matrix, (pairs[:, 0], pairs[:, 1]), pairs[:, 2])
    return TaktDemand(zones=zones, matrix=matrix, production=production, points=points)


def load_demand_streets(path: str | Path) -> list[tuple[tuple[tuple[float, float], ...], float]]:
    """Читает ``demand-streets.json`` (FeatureCollection рёбер спроса).

    Возвращает ``[(координаты, вес), ...]``; вес — свойство ``d`` ребра.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    features = data.get("features") if isinstance(data, dict) else None
    if features is None:
        raise OdMatrixError(f"Файл demand-streets.json не содержит features: {path}")
    edges: list[tuple[tuple[tuple[float, float], ...], float]] = []
    for item in features:
        geom = (item or {}).get("geometry") or {}
        coords = geom.get("coordinates")
        if geom.get("type") != "MultiLineString" or not coords:
            continue
        props = item.get("properties") or {}
        try:
            weight = float(props.get("d", 1.0))
        except (TypeError, ValueError):
            weight = 1.0
        for part in coords:
            rings = tuple(
                tuple((float(c[0]), float(c[1])) for c in part) if part else ()
            )
            if len(rings) > 1:
                edges.append((rings, weight))
    return edges


def _street_zone_contribs(
    coords: tuple[tuple[float, float], ...],
    weight: float,
    *,
    scale: float,
    tree: cKDTree,
    step_m: float = 50.0,
) -> dict[int, float]:
    """Раскладка веса ребра по зонам: ``{индекс зоны: длина×вес}``.

    Полилиния режется на участки шагом ``step_m`` (метры); середина каждого
    участка относится к ближайшей зоне, доля веса пропорциональна длине
    участка. В отличие от привязки к середине ребра, вес на границе зон
    распределяется между зонами, а не уходит целиком в одну из них.
    """
    contribs: dict[int, float] = {}
    for (lon1, lat1), (lon2, lat2) in itertools.pairwise(coords):
        seg_m = haversine_km((lon1, lat1), (lon2, lat2)) * 1000.0
        if seg_m <= 0.0:
            continue
        parts = max(math.ceil(seg_m / step_m), 1)
        per = weight * seg_m / parts
        dlon, dlat = lon2 - lon1, lat2 - lat1
        for part in range(parts):
            t = (part + 0.5) / parts
            idx = tree.query(((lon1 + dlon * t) * scale, lat1 + dlat * t))[1]
            contribs[idx] = contribs.get(idx, 0.0) + per
    return contribs


def _write_demand_street_geojson(
    zones: Zones,
    edges: Sequence[tuple[tuple[tuple[float, float], ...], float]],
    path: Path,
) -> None:
    """Сохраняет рёбра спроса с весами в GeoJSON.

    Ребро размножается на фичи по зонам: ``d`` — доля веса ребра, пришёдшаяся
    на зону, ``zone_id`` — сама зона (аналог ``_street_zone_contribs``).
    """
    scale = _cosscale(zones.xy)
    tree = cKDTree(zones.xy * [scale, 1.0])
    features: list[dict[str, Any]] = []
    for coords, weight in edges:
        if len(coords) < 2:
            continue
        for zone_idx, contrib in _street_zone_contribs(
            coords, weight, scale=scale, tree=tree
        ).items():
            features.append(
                {
                    "type": "Feature",
                    "properties": {"d": contrib, "zone_id": zones.ids.tolist()[zone_idx]},
                    "geometry": {
                        "type": "MultiLineString",
                        "coordinates": [list(coords)],
                    },
                }
            )
    path.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}),
        encoding="utf-8",
    )


def zone_weights_from_streets(
    zones: Zones,
    edges: Sequence[tuple[tuple[tuple[float, float], ...], float]],
    *,
    step_m: float = 50.0,
) -> np.ndarray:
    """Веса зон по рёбрам спроса: ``сумма длина_(участка)×вес`` по зонам.

    Каждое ребро режется на участки шагом ``step_m``; вес ребра попадает
    в зоны пропорционально попавшей в них длине (вместо целиком в зону
    середины ребра). Возвращается вектор ``production`` (= ``attraction``
    по построению).
    """

    weights = np.zeros(len(zones), dtype=np.float64)
    scale = _cosscale(zones.xy)
    tree = cKDTree(zones.xy * [scale, 1.0])
    xs: list[float] = []
    ys: list[float] = []
    parts: list[float] = []
    for coords, weight in edges:
        if weight <= 0.0 or len(coords) < 2:
            continue
        for (lon1, lat1), (lon2, lat2) in itertools.pairwise(coords):
            seg_m = haversine_km((lon1, lat1), (lon2, lat2)) * 1000.0
            if seg_m <= 0.0:
                continue
            count = max(math.ceil(seg_m / step_m), 1)
            per = weight * seg_m / count
            dlon, dlat = lon2 - lon1, lat2 - lat1
            for part in range(count):
                t = (part + 0.5) / count
                xs.append(lon1 + dlon * t)
                ys.append(lat1 + dlat * t)
                parts.append(per)
    if xs:
        pts = np.column_stack(
            (np.asarray(xs, dtype=float) * scale, np.asarray(ys, dtype=float))
        )
        _, nearest = tree.query(pts, k=1)
        np.add.at(weights, nearest, np.asarray(parts, dtype=float))
    return weights


def _cell_step(size_m: float, mean_lat: float) -> tuple[float, float]:
    lat_deg = float(size_m) / _KILOMETERS_PER_DEGREE / 1000.0
    lon_deg = lat_deg / max(math.cos(math.radians(mean_lat)), 0.2)
    return lon_deg, lat_deg


def build_zones(boundary: Any, *, size_m: float = _DEFAULT_ZONE_SIZE_M) -> Zones:
    """Строит сетку ячеек внутри границы (целые/обрезанные по границе).

    Координаты ячеек выводятся от угла границы целыми шагами сетки
    (``minx + col*lon_deg``), чтобы многократное ``x += lon_deg`` не
    накапливало дрейф плавающей точки на больших сетках.
    """
    minx, miny, maxx, maxy = boundary.bounds
    lon_deg, lat_deg = _cell_step(size_m, (miny + maxy) / 2.0)
    min_area = _MIN_CLIP_AREA_RATIO * lon_deg * lat_deg
    polygons: list[Any] = []
    row = 0
    while True:
        y = miny + row * lat_deg
        if y >= maxy:
            break
        col = 0
        while True:
            x = minx + col * lon_deg
            if x >= maxx:
                break
            clip = box(x, y, x + lon_deg, y + lat_deg).intersection(boundary)
            if not clip.is_empty and clip.area >= min_area:
                polygons.append(clip)
            col += 1
        row += 1
    if not polygons:
        raise OdMatrixError("Зонирование не дало ни одной зоны внутри границы")
    ids = np.arange(1, len(polygons) + 1, dtype=np.int64)
    xy = np.asarray([(p.centroid.x, p.centroid.y) for p in polygons], dtype=float)
    return Zones(ids=ids, polygons=tuple(polygons), xy=xy, bounds=boundary.bounds)


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Расстояние между точками ``(lon, lat)`` по формуле гаверсинуса, км."""
    lat1, lon1 = math.radians(a[1]), math.radians(a[0])
    lat2, lon2 = math.radians(b[1]), math.radians(b[0])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = (
        math.sin(dlat / 2.0) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2.0) ** 2
    )
    return 6371.0 * 2.0 * math.asin(math.sqrt(h))


def _way_speed(highway: str | None) -> float:
    return _ROAD_SPEED_KMH.get(str(highway or "").lower(), _FALLBACK_SPEED_KMH)


# Округление узлов графа: ~0.1 м по широте. Позволяет склеивать координаты
# пересечений, различающиеся лишь хвостом float, не сливая соседние полосы.
_NODE_SNAP_DECIMALS = 6


def _snap_coord(coord: tuple[float, float]) -> tuple[float, float]:
    return (round(coord[0], _NODE_SNAP_DECIMALS), round(coord[1], _NODE_SNAP_DECIMALS))


def _cosscale(*points: np.ndarray) -> float:
    """cos(средней широты) — местный масштаб долготы для евклидовых NN.

    Bез масштаба cKDTree ищет соседей в декартовых градусах, где 1° долготы
    короче 1° широты в ``cos(lat)`` раз; для городов с широтами ≲60° хвост
    ошибки достигает сотен метров даже в пределах агломерации.
    """
    latitudes = np.concatenate([np.asarray(p)[:, 1] for p in points]) if points else np.empty(0)
    if latitudes.size == 0:
        return 1.0
    return float(np.cos(np.deg2rad(np.mean(latitudes))))


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


def _furness(
    matrix: np.ndarray,
    origins: np.ndarray,
    attractions: np.ndarray,
    *,
    max_iter: int = _FURNESS_MAX_ITER,
    tol: float = _FURNESS_TOL,
) -> np.ndarray:
    """Балансирует матрицу под суммы строк/столбцов (итерации Фёрнесс)."""
    balanced = np.asarray(matrix, dtype=np.float64).copy()
    for _ in range(max_iter):
        rows = balanced.sum(axis=1)
        balanced *= np.divide(
            origins, rows, out=np.zeros_like(origins), where=rows > 0
        )[:, None]
        cols = balanced.sum(axis=0)
        balanced *= np.divide(
            attractions, cols, out=np.zeros_like(attractions), where=cols > 0
        )[None, :]
        error = max(
            float(np.max(np.abs(balanced.sum(axis=1) - origins))),
            float(np.max(np.abs(balanced.sum(axis=0) - attractions))),
        )
        if error < tol:
            break
    return balanced


def _furness_sparse(
    matrix: sparse.csr_matrix,
    origins: np.ndarray,
    attractions: np.ndarray,
    *,
    max_iter: int = _FURNESS_MAX_ITER,
    tol: float = _FURNESS_TOL,
) -> np.ndarray:
    """Разреженный Фёрнесс: только для больших матриц (строки без контакта
    остаются нулевыми, дорогостоящая плотная арифметика не выполняется)."""
    balanced = matrix.astype(np.float64)
    for _ in range(max_iter):
        rows = np.asarray(balanced.sum(axis=1)).ravel()
        balanced = balanced.multiply(
            np.divide(origins, rows, out=np.zeros_like(origins), where=rows > 0)[:, None]
        )
        cols = np.asarray(balanced.sum(axis=0)).ravel()
        balanced = balanced.multiply(
            np.divide(
                attractions, cols, out=np.zeros_like(attractions), where=cols > 0
            )[None, :]
        )
        balanced.eliminate_zeros()
        error = max(
            float(np.max(np.abs(np.asarray(balanced.sum(axis=1)).ravel() - origins))),
            float(np.max(np.abs(np.asarray(balanced.sum(axis=0)).ravel() - attractions))),
        )
        if error < tol:
            break
    return balanced.toarray()


def build_gravity_od(
    weights: np.ndarray,
    cost: np.ndarray,
    *,
    attraction: np.ndarray | None = None,
    decay_minutes: float | None = None,
) -> np.ndarray:
    """Гравитационная матрица OD: ``sum(i,j) ~ p_i*a_j*exp(-beta*t)``.

    ``weights`` — отправления (производство, напр. население из растра),
    ``attraction`` — рабочие места (по умолчанию равно weights; если задан
    отдельно, нормируется на итог производства для сходимости Фёрнесса).
    ``decay_minutes`` — время, при котором ``exp(-beta*t) = 1/e`` (параметр
    гравитации; по умолчанию ``_DECAY_RADIUS_KM / _GRAVITY_REF_SPEED_KMH * 60``).
    Для больших сетей (``n**2 > _SPARSE_GRAVITY_CELLS``) ядро строится
    разреженным: слабые дальние взаимодействия отбрасываются как
    ``kernel < _GRAVITY_KERNEL_EPS``, плотный ``np.outer`` не выполняется.
    """
    production = np.asarray(weights, dtype=np.float64).copy()
    if attraction is None:
        attraction = production.copy()
    else:
        attraction = np.asarray(attraction, dtype=np.float64).copy()
        if attraction.shape != production.shape:
            raise OdMatrixError("Длины production и attraction не совпадают")
    if production.sum() <= 0.0 or attraction.sum() <= 0.0:
        raise OdMatrixError("Сумма весов зон равна нулю")
    if float(production.sum()) != float(attraction.sum()):
        attraction = attraction * production.sum() / attraction.sum()
    if decay_minutes is None:
        decay_minutes = _DECAY_RADIUS_KM / _GRAVITY_REF_SPEED_KMH * 60.0
    if decay_minutes <= 0.0:
        raise OdMatrixError("decay_minutes должен быть положительным")
    beta = 1.0 / decay_minutes
    impedance = np.where(np.isfinite(cost), cost, 1e6)
    if impedance.size > _SPARSE_GRAVITY_CELLS:
        seed = _sparse_gravity_seed(production, attraction, beta, impedance)
        return _furness_sparse(seed, production, attraction)
    kernel = np.exp(-beta * impedance)
    np.fill_diagonal(kernel, 0.0)
    kernel[~np.isfinite(cost)] = 0.0
    seed = np.outer(production, attraction) * kernel
    return _furness(seed, production, attraction)


def _sparse_gravity_seed(
    production: np.ndarray,
    attraction: np.ndarray,
    beta: float,
    impedance: np.ndarray,
) -> sparse.csr_matrix:
    """Разреженное гравитационное ядро: ``diag(p) @ exp(-beta*t) @ diag(a)``.

    Значения ниже ``_GRAVITY_KERNEL_EPS`` (порог ``t > ln(1/eps)/beta``)
    отбрасываются; диагональ обнуляется, не-конечные стоимости дают 0.
    """
    kernel = np.exp(-beta * impedance)
    kernel[kernel < _GRAVITY_KERNEL_EPS] = 0.0
    np.fill_diagonal(kernel, 0.0)
    kernel[~np.isfinite(impedance)] = 0.0
    seed = (
        sparse.diags(production) @ sparse.csr_matrix(kernel) @ sparse.diags(attraction)
    )
    seed.eliminate_zeros()
    return seed.tocsr()


_DEFAULT_PURPOSE_PERIOD_SLOTS: tuple[tuple[str, str], ...] = (
    ("early", "04–06"),
    ("am", "06–09"),
    ("mid", "09–15"),
    ("pm", "15–19"),
    ("eve", "19–24"),
)
_MAX_DISTRICT_RADIUS_M = 30000.0


def periods_from_purpose_blend(
    out: Sequence[float], ret: Sequence[float]
) -> tuple[Period, ...]:
    """Периоды суток из взвешенных профилей целей (слоты Takt 04–06…19–24)."""
    if len(out) != len(_DEFAULT_PURPOSE_PERIOD_SLOTS):
        raise OdMatrixError(
            f"Профиль целей содержит {len(out)} слотов, "
            f"ожидается {len(_DEFAULT_PURPOSE_PERIOD_SLOTS)}"
        )
    return tuple(
        Period(key=key, label=label, out=float(o), ret=float(r))
        for (key, label), o, r in zip(_DEFAULT_PURPOSE_PERIOD_SLOTS, out, ret)
    )


def _centroid_dist_km(zones: Zones) -> np.ndarray:
    """Матрица гаверсинус-расстояний между центроидами зон, км."""
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
    return dist_km


def _round_half_up(value: float) -> int:
    """Округление как ``Math.round`` движка JS (половина — вверх)."""
    return math.floor(value + 0.5)


def build_takt_od(
    points: np.ndarray,
    attract: np.ndarray,
    *,
    trips_per_res: float,
    d0_m: float,
    k: int,
    longitude_m_per_degree: float | None = None,
    latitude_m_per_degree: float = _TAKT_M_PER_DEG_LAT,
) -> np.ndarray:
    """OD-пары по алгоритму движка Takt (функция ``xn``).

    ``points`` — массив ``[точка, (lon, lat, pop)]``; ``attract`` — массив
    притяжения каждой точки. Группа аттракторов сворачивается в сетку
    ``0.01°`` долготы и ``0.01° * 0.62`` широты; представитель ячейки —
    точка с максимальным притяжением. Отправления точек с ``pop >= 40``:
    ``pop * trips_per_res``. Сопротивление ``exp(-d / d0)``; назначения
    делятся на 4 дистанционных диапазона ``[0, d0, 2.5d0, 6d0, inf]``,
    по ``max(1, round(k / 4))`` лучших направлений на диапазон. Поездки
    округляются по правилу наибольшего остатка. Время поездки
    ``round(d * 1.35 / 7.5 + 240)`` секунд. Балансировки Фёрнесса нет.

    Результат — массив ``[OD-пара, 4]``: ``[origin, dest, trips, seconds]``.
    """
    pts = np.asarray(points, dtype=np.float64)
    if pts.ndim != 2 or pts.shape[1] != 3:
        raise OdMatrixError("points должны быть массивом [точка, (lon, lat, pop)]")
    att = np.asarray(attract, dtype=np.float64)
    if att.shape != (len(pts),):
        raise OdMatrixError("attract не совпадает с числом точек")
    if d0_m <= 0.0:
        raise OdMatrixError("d0_m должен быть положительным")
    if longitude_m_per_degree is None:
        mean_lat = float(np.mean(pts[:, 1]))
        longitude_m_per_degree = (
            _TAKT_M_PER_DEG_LON_EQUATOR * math.cos(math.radians(mean_lat))
        )
    p = (0.0, float(d0_m), 2.5 * float(d0_m), 6.0 * float(d0_m), math.inf)
    h = 8.0 * float(d0_m)
    w = max(1, _round_half_up(k / 4))
    n_bands = len(p) - 1

    cells: dict[tuple[int, int], list[float | int]] = {}
    for idx, (g, pop) in enumerate(zip(att, pts[:, 2])):
        if g <= 0.0:
            continue
        key = (
            math.floor(pts[idx, 0] / _TAKT_OD_GRID_LON_DEG),
            math.floor(pts[idx, 1] / (_TAKT_OD_GRID_LON_DEG * _TAKT_OD_GRID_LAT_RATIO)),
        )
        cell = cells.get(key)
        if cell is None:
            cell = [0.0, idx, g, pts[idx, 0], pts[idx, 1]]
            cells[key] = cell
        cell[0] += g
        if g > cell[2]:
            cell[1] = idx
            cell[2] = g
            cell[3] = pts[idx, 0]
            cell[4] = pts[idx, 1]

    groups = list(cells.values())
    out: list[list[int]] = []
    for origin in range(len(pts)):
        if pts[origin, 2] < _TAKT_OD_POP_CUTOFF:
            continue
        total_trips = pts[origin, 2] * float(trips_per_res)
        bands: list[list[tuple[int, float, float]]] = [[] for _ in range(n_bands)]
        band_e = [0.0] * n_bands
        total_e = 0.0
        for cell_e, best, _, rep_lon, rep_lat in groups:
            if best == origin:
                continue
            dist = math.hypot(
                (rep_lon - pts[origin, 0]) * longitude_m_per_degree,
                (rep_lat - pts[origin, 1]) * latitude_m_per_degree,
            )
            if dist > h or dist < _TAKT_OD_MIN_DIST_M:
                continue
            e = cell_e * math.exp(-dist / d0_m)
            if not e > 0.0:
                continue
            band = np.searchsorted(p, dist, side="right") - 1
            bands[band].append((best, e, dist))
            band_e[band] += e
            total_e += e
        if total_e <= 0.0:
            continue
        for band in range(n_bands):
            if band_e[band] <= 0.0:
                continue
            ranked = sorted(bands[band], key=lambda item: item[1], reverse=True)[:w]
            ranked_e = sum(item[1] for item in ranked)
            trips = total_trips * band_e[band] / total_e
            carry = 0.0
            for dest, e, dist in ranked:
                value = trips * e / ranked_e + carry
                n_trips = _round_half_up(value)
                carry = value - n_trips
                if n_trips > 0:
                    time_s = _round_half_up(
                        dist * _TAKT_OD_CIRCUITY / _TAKT_OD_SPEED_MPS + _TAKT_OD_BASE_SECONDS
                    )
                    out.append([origin, dest, n_trips, time_s])
    if not out:
        return np.zeros((0, 4), dtype=np.int64)
    return np.asarray(out, dtype=np.int64)


def build_purpose_od(
    production: np.ndarray,
    zones: Zones,
    *,
    purposes: Sequence[Purpose] = PURPOSE_DEFAULTS,
    engine: str = "takt",
) -> PurposeOd:
    """OD по целям поездок.

    ``engine="takt"`` (по умолчанию): алгоритм движка Takt
    (``build_takt_od``) — сетка аттракторов 0.01°, экспоненциальное
    затухание и целые поездки без балансировки. ``engine="gravity"``: для
    каждой цели ``p`` отправления ``production * trips_per_res``,
    притяжение равно отправлениям, сопротивление — расстояние между
    центроидами зон: доля поездок на дистанцию ``d`` пропорциональна
    ``(1 + d/d0)^(-k)`` с балансировкой Фёрнесс. Матрицы целей
    суммируются; профили периодов суток взвешиваются долями поездок
    каждой цели.
    """
    if engine not in ("gravity", "takt"):
        raise OdMatrixError(f"Неизвестный движок OD по целям: {engine!r}")
    prod = np.asarray(production, dtype=np.float64)
    if prod.shape != (len(zones),):
        raise OdMatrixError("production не совпадает с числом зон")
    if prod.sum() <= 0.0:
        raise OdMatrixError("Сумма весов зон равна нулю")
    dist_km = _centroid_dist_km(zones)
    matrices: list[np.ndarray] = []
    totals: list[float] = []
    used: list[Purpose] = []
    for purpose in purposes:
        prod_p = prod * float(purpose.trips_per_res)
        if prod_p.sum() <= 0.0:
            continue
        if engine == "takt":
            points = np.column_stack((zones.xy, prod))
            pairs = build_takt_od(
                points,
                prod,
                trips_per_res=float(purpose.trips_per_res),
                d0_m=float(purpose.d0_m),
                k=purpose.k,
            )
            matrix_p = np.zeros((len(zones), len(zones)), dtype=np.float64)
            if len(pairs):
                matrix_p[pairs[:, 0], pairs[:, 1]] = pairs[:, 2]
        else:
            d0_km = max(float(purpose.d0_m) / 1000.0, 1e-3)
            kernel = (1.0 + dist_km / d0_km) ** (-int(purpose.k))
            np.fill_diagonal(kernel, 0.0)
            seed = np.outer(prod_p, prod_p) * kernel
            matrix_p = _furness(seed, prod_p, prod_p)
        matrices.append(matrix_p)
        used.append(purpose)
        totals.append(float(matrix_p.sum()))
    if not matrices:
        raise OdMatrixError("Ни одна цель не дала поездок")
    matrix = np.sum(matrices, axis=0)
    total = max(float(matrix.sum()), 1.0)
    shares = tuple(t / total for t in totals)
    common_len = max(len(p.out) for p in used)
    out_weights = np.zeros(common_len)
    ret_weights = np.zeros(common_len)
    for share, purpose in zip(shares, used):
        pad_out = tuple(purpose.out) + (0.0,) * (common_len - len(purpose.out))
        pad_ret = tuple(purpose.ret) + (0.0,) * (common_len - len(purpose.ret))
        out_weights += np.asarray(pad_out) * share
        ret_weights += np.asarray(pad_ret) * share
    return PurposeOd(
        matrix=matrix,
        purpose_matrices=tuple(matrices),
        period_out=tuple(float(v) for v in out_weights),
        period_ret=tuple(float(v) for v in ret_weights),
        shares=shares,
        purposes=tuple(used),
    )


def load_districts(path: str | Path) -> list[tuple[str, Any]]:
    """Загружает районы/места: ``[{'n': name, 'x': lon, 'y': lat}]``
    (формат page_assets districts.json) либо GeoJSON FeatureCollection.
    Возвращает ``[(имя, геометрия), ...]`` без дубликатов имён.
    """
    import json as _json

    data = _json.loads(Path(path).read_text(encoding="utf-8"))
    places: list[tuple[str, Any]] = []
    if isinstance(data, dict) and isinstance(data.get("places"), list):
        for item in data["places"]:
            name = str(item.get("n") or item.get("name") or "").strip()
            if not name:
                continue
            if "x" in item and "y" in item:
                geometry = Point(float(item["x"]), float(item["y"]))
            else:
                continue
            places.append((name, geometry))
    elif isinstance(data, dict) and data.get("type") == "FeatureCollection":
        for feature in data.get("features", []):
            props = feature.get("properties") or {}
            name = str(props.get("name") or props.get("NAME") or "").strip()
            geometry = feature.get("geometry")
            if not name or geometry is None:
                continue
            places.append((name, geometry))
    seen: set[str] = set()
    unique: list[tuple[str, Any]] = []
    for name, geometry in places:
        if name in seen:
            continue
        seen.add(name)
        unique.append((name, geometry))
    if not unique:
        raise OdMatrixError(f"Файл районов {path} не содержит ни одного места")
    return unique


def _representative_point(geometry: Any) -> Point | None:
    """Точка для поиска: Point — сама, LineString/Polygon — представитель."""
    import shapely.geometry as sg

    if geometry is None or geometry.is_empty:
        return None
    if geometry.geom_type in ("Point",):
        return geometry
    if geometry.geom_type in ("LineString", "MultiLineString"):
        pts = list(geometry.coords) if geometry.geom_type == "LineString" else [
            p for part in geometry.geoms for p in part.coords
        ]
        if not pts:
            return None
        mid = pts[len(pts) // 2]
        return sg.Point(mid)
    if geometry.geom_type in ("Polygon", "MultiPolygon"):
        return geometry.representative_point()
    return None


def assign_district_names(zones: Zones, places: list[tuple[str, Any]]) -> tuple[str, ...]:
    """Имена ближайших районов для каждой зоны (пустая строка, если ближе
    ``_MAX_DISTRICT_RADIUS_M`` ничего нет)."""
    points: list[tuple[float, float]] = []
    names: list[str] = []
    for name, geometry in places:
        rep = _representative_point(geometry)
        if rep is not None and rep.is_empty is False:
            points.append((rep.x, rep.y))
            names.append(name)
    if not points:
        return tuple("" for _ in range(len(zones)))
    array = np.asarray(points, dtype=float)
    scale = _cosscale(array, zones.xy)
    tree = cKDTree(array * [scale, 1.0])
    _, nearest = tree.query(zones.xy * [scale, 1.0], k=1)
    result: list[str] = []
    for i in range(len(zones)):
        x1, y1 = zones.xy[i]
        idx = int(nearest[i])
        x2, y2 = points[idx]
        dist_m = haversine_km((x1, y1), (x2, y2)) * 1000.0
        result.append(names[idx] if dist_m <= _MAX_DISTRICT_RADIUS_M else "")
    return tuple(result)


def _tile_is_geographic(tile: dict[str, Any]) -> bool:
    return bool(getattr(tile.get("crs"), "is_geographic", False))


def _tile_bounds_in(
    tile: dict[str, Any], warp: Any, bounds: tuple[float, float, float, float]
) -> tuple[float, float, float, float]:
    if _tile_is_geographic(tile):
        return bounds
    xs, ys = warp.transform(
        "EPSG:4326",
        tile["crs"],
        [bounds[0], bounds[2], bounds[0], bounds[2]],
        [bounds[1], bounds[1], bounds[3], bounds[3]],
    )
    return (min(xs), min(ys), max(xs), max(ys))


def _tile_overlaps_bounds(
    bounds: tuple[float, float, float, float], tile: dict[str, Any]
) -> bool:
    tile_bounds = tile.get("bounds")
    return not (
        bounds[0] > tile_bounds.right
        or bounds[2] < tile_bounds.left
        or bounds[1] > tile_bounds.top
        or bounds[3] < tile_bounds.bottom
    )


def _reproject_polygon(poly: Any, warp: Any, target_crs: Any) -> Any:
    def _apply(x: Any, y: Any) -> tuple[Any, Any]:
        tx, ty = warp.transform("EPSG:4326", target_crs, x.tolist(), y.tolist())
        return np.asarray(tx), np.asarray(ty)

    return geom_transform(_apply, poly)


def _zone_tile_sums(
    zones: Zones, tile: dict[str, Any], *, stack: dict[str, Any]
) -> np.ndarray:
    """Вклад одного тайла: сумма значений растра внутри каждой зоны."""
    rio_windows = stack["windows"]
    rio_features = stack["features"]
    warp = stack["warp"]
    ds = tile["ds"]
    inner = _tile_bounds_in(tile, warp, zones.bounds)
    if not _tile_overlaps_bounds(inner, tile):
        return np.zeros(len(zones))
    is_geo = _tile_is_geographic(tile)
    pad = _GEO_PAD_DEG if is_geo else _PROJECTED_PAD_M
    win = rio_windows.from_bounds(
        inner[0] - pad, inner[1] - pad, inner[2] + pad, inner[3] + pad, ds.transform
    )
    win = win.round_lengths().round_offsets()
    full = rio_windows.Window(0, 0, ds.width, ds.height)
    win = rio_windows.intersection(win, full)
    if win is None or win.width <= 0 or win.height <= 0:
        return np.zeros(len(zones))
    arr = ds.read(1, window=win).astype("float64")
    win_transform = rio_windows.transform(win, ds.transform)
    shapes = []
    for i, poly in enumerate(zones.polygons, start=1):
        target = poly if is_geo else _reproject_polygon(poly, warp, tile["crs"])
        shapes.append((target, i))
    labels = rio_features.rasterize(
        shapes,
        out_shape=(int(win.height), int(win.width)),
        transform=win_transform,
        fill=0,
    )
    flat = arr.reshape(-1)
    names = labels.astype(np.int64).reshape(-1)
    good = np.isfinite(flat) & (flat > 0.0)
    sums = np.bincount(names[good], weights=flat[good], minlength=len(zones) + 1)
    return sums[1:]


def zonal_weights(
    zones: Zones, raster_path: str | None, *, stack: dict[str, Any] | None = None
) -> np.ndarray:
    """Веса зон: сумма значений GHS-растра внутри зоны; без растра — единицы."""
    stack = stack or raster_stack()
    rasterio = stack["rasterio"]
    paths = resolve_sources(raster_path, (".tif", ".tiff"))
    if not paths:
        return np.ones(len(zones))
    index = open_raster_index(paths, rasterio)
    if not index:
        return np.ones(len(zones))
    total = np.zeros(len(zones), dtype=np.float64)
    try:
        for tile in index:
            total += _zone_tile_sums(zones, tile, stack=stack)
    finally:
        for tile in index:
            with contextlib.suppress(OSError, ValueError, RuntimeError):
                tile["ds"].close()
    if float(total.sum()) <= 0.0:
        return np.ones(len(zones))
    return total


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
    from .overture_load import load_overture_segments

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


def _write_frame(frame: Any, path: Path) -> Path:
    """Пишет DataFrame в parquet, а при отсутствии pyarrow — в CSV."""
    try:
        frame.to_parquet(path)
        return path
    except Exception:  # noqa: BLE001 — fallback без тяжёлой зависимости
        csv_path = path.with_suffix(".csv")
        frame.to_csv(csv_path, index=False, encoding="utf-8-sig")
        return csv_path


def _read_frame(path: str | Path) -> Any:
    """Читает DataFrame из parquet или CSV (обратный `_write_frame`)."""
    import pandas as pd

    if Path(path).suffix.lower() == ".csv":
        return pd.read_csv(path)
    return pd.read_parquet(path)


def _write_zones_geojson(
    zones: Zones, path: Path, district_names: Sequence[str] | None = None
) -> None:
    features = [
        {
            "type": "Feature",
            "properties": {
                "zone_id": int(zones.ids[i]),
                **({"district": district_names[i]} if district_names else {}),
            },
            "geometry": shapely_mapping(zones.polygons[i]),
        }
        for i in range(len(zones))
    ]
    path.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}),
        encoding="utf-8",
    )


def save_od_outputs(
    zones: Zones,
    matrix: np.ndarray,
    *,
    city: str,
    out_dir: str | Path | None = None,
    production: np.ndarray | None = None,
    attraction: np.ndarray | None = None,
    purpose_trips: Sequence[float] | None = None,
    purpose_keys: Sequence[str] | None = None,
    district_names: Sequence[str] | None = None,
    write_matrix_frame: bool = True,
) -> list[Path]:
    """Сохраняет матрицу, пары и зоны; возвращает записанные пути.

    Колонки ``production``/``attraction`` (если заданы) попадают в файл зон
    в формате Tranmodel, чтобы результат можно было перечитать через
    ``load_zones_from_file``/``load_od_from_files``. При заданных
    ``purpose_trips``/``purpose_keys`` пишется разбиение по целям поездок,
    при ``district_names`` — названия районов для каждой зоны.
    ``write_matrix_frame=False`` пропускает плотный ``od_matrix.parquet``
    (для больших городов) — матрица восстанавливается из ``od_pairs``.
    """
    import geopandas as gpd
    import pandas as pd

    out = Path(out_dir or ".")
    out.mkdir(parents=True, exist_ok=True)
    ids = zones.ids.tolist()
    written: list[Path] = []
    if write_matrix_frame:
        frames = pd.DataFrame(matrix, index=ids, columns=ids)
        written.append(
            _write_frame(frames, out / f"{city}_od_matrix.parquet")
        )
    rows, cols = np.nonzero(matrix > 0)
    ids_arr = np.asarray(ids, dtype=np.int64)
    pairs = pd.DataFrame(
        {
            "orig_zone": ids_arr[rows],
            "dest_zone": ids_arr[cols],
            "trips": matrix[rows, cols],
        }
    )
    pairs = pairs[pairs["orig_zone"] != pairs["dest_zone"]]
    pairs = pairs.sort_values("trips", ascending=False)
    written.append(_write_frame(pairs, out / f"{city}_od_pairs.parquet"))
    if purpose_trips is not None:
        keys = list(purpose_keys or ())
        total = max(float(sum(purpose_trips)), 1.0)
        purpose_frame = pd.DataFrame(
            {
                "purpose": keys,
                "trips": [float(v) for v in purpose_trips],
                "share": [float(v) / total for v in purpose_trips],
            }
        )
        written.append(_write_frame(purpose_frame, out / f"{city}_od_purposes.parquet"))
    zones_path = out / f"{city}_od_zones.geojson"
    _write_zones_geojson(zones, zones_path, district_names=district_names)
    written.append(zones_path)
    zdf = gpd.GeoDataFrame(
        {"zone_id": zones.ids, "geometry": list(zones.polygons)},
        crs="EPSG:4326",
    )
    if production is not None:
        zdf["production"] = np.asarray(production)
    if attraction is not None:
        zdf["attraction"] = np.asarray(attraction)
    if district_names is not None:
        zdf["district"] = list(district_names)
    written.append(_write_frame(zdf, out / f"{city}_od_zones.parquet"))
    return written


def assign_road_loads(
    zones: Zones,
    matrix: np.ndarray,
    graph: nx.Graph,
    reporter: Any | None = None,
) -> dict[tuple[Any, Any], float]:
    """Распределяет поездки OD на рёбра сети (All-or-Nothing).

    Каждая пара ``(i, j)`` направляется по кратчайшему (во времени) пути
    между центрами зон; поток добавляется на все рёбра этого пути.
    Возвращает ``{упорядоченная пара узлов: поездок}``. Пары без пути
    (разрывы сети) не присваиваются; при заданном ``reporter`` выводится
    предупреждение с числом нераспределённых поездок.
    """
    snapped = _snapped_centroids(zones, graph)
    loaded: dict[tuple[Any, Any], float] = {}
    # Один Dijkstra на уникальный узел привязки вместо одного на зону.
    grouped: dict[Any, list[int]] = {}
    for i, source in enumerate(snapped):
        grouped.setdefault(source, []).append(i)
    total_trips = 0.0
    unassigned_trips = 0.0
    for source, indices in grouped.items():
        _, paths = nx.single_source_dijkstra(graph, source, weight="time")
        for i in indices:
            marker_rows = np.nonzero(matrix[i])[0] if matrix.shape[0] else ()
            for j in marker_rows:
                if i == j:
                    continue
                trips = float(matrix[i, j])
                if trips <= 0.0:
                    continue
                total_trips += trips
                nodes = paths.get(snapped[j])
                if not nodes:
                    unassigned_trips += trips
                    continue
                for a, b in itertools.pairwise(nodes):
                    edge = (a, b) if a < b else (b, a)
                    loaded[edge] = loaded.get(edge, 0.0) + trips
    if reporter is not None and unassigned_trips > 0.0:
        share = unassigned_trips / max(total_trips, 1e-9) * 100.0
        reporter.line(
            f"  Не распределено на сеть: {unassigned_trips:,.0f} поездок "
            f"({share:.1f}%) — проверьте связность дорожной сети"
        )
    return loaded


def save_road_load_outputs(
    loads: dict[tuple[Any, Any], float],
    *,
    city: str,
    out_dir: str | Path | None = None,
) -> list[Path]:
    """Сохраняет загрузку рёбер (pairs + geojson); возвращает записанные пути."""
    import pandas as pd

    out = Path(out_dir or ".")
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    if not loads:
        return written
    edges = sorted(loads.keys(), key=lambda e: (-loads[e], e))
    frame = pd.DataFrame(
        {
            "node_a": [a for a, _ in edges],
            "node_b": [b for _, b in edges],
            "trips": [loads[e] for e in edges],
        }
    )
    written.append(_write_frame(frame, out / f"{city}_od_road_loads.parquet"))
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"trips": float(loads[e])},
                "geometry": {
                    "type": "LineString",
                    "coordinates": [list(e[0]), list(e[1])],
                },
            }
            for e in edges
        ],
    }
    path = out / f"{city}_od_road_loads.geojson"
    path.write_text(json.dumps(geojson, ensure_ascii=False, indent=1), encoding="utf-8")
    written.append(path)
    return written


def load_zones_from_file(
    zones_path: str | Path,
    *,
    reporter: Any = None,
) -> tuple[Zones, np.ndarray, np.ndarray]:
    """Загружает зоны и веса из файла внешней модели (формат Tranmodel).

    Возвращает ``(zones, production, attraction)``: отправления берутся из
    колонки ``production`` (или ``population``), притяжение — из ``attraction``
    (или ``jobs``). Геометрия перепроецируется в EPSG:4326, центроиды считаются
    в исходной (метрической) проекции.
    """
    import geopandas as gpd

    line = getattr(reporter, "line", None)
    if line:
        line(f"  Загрузка зон из файла: {zones_path}")
    if Path(zones_path).suffix.lower() == ".csv":
        # CSV-фолбэк `_write_frame`: геометрия записана WKT-строкой.
        import pandas as pd

        zdf = pd.read_csv(zones_path)
        if "geometry" not in zdf.columns:
            raise OdMatrixError(
                f"Файл зон {zones_path} не содержит колонки geometry"
            )
        geometry = gpd.GeoSeries.from_wkt(zdf["geometry"])
        zdf = gpd.GeoDataFrame(
            zdf.drop(columns=["geometry"]),
            geometry=geometry,
            crs="EPSG:4326",
        )
    else:
        zdf = gpd.read_parquet(zones_path)
    zdf = zdf.sort_values("zone_id").reset_index(drop=True)
    ids = zdf.zone_id.to_numpy(dtype=np.int64)
    center_crs = zdf.crs if zdf.crs is not None else 4326
    # Центроиды считаем в метрической проекции (точнее), исходную не трогаем.
    # Для географического CRS подбираем локальную UTM-зону по средней долготе,
    # а полушарие — по средней широте (326xx — север, 327xx — юг).
    if bool(getattr(center_crs, "is_geographic", False)):
        minx, miny, maxx, maxy = zdf.total_bounds
        mean_lon = float((minx + maxx) / 2.0)
        mean_lat = float((miny + maxy) / 2.0)
        zone = int((mean_lon + 180.0) // 6.0) + 1
        zone = min(max(zone, 1), 60)
        utm_epsg = 32600 + zone if mean_lat >= 0.0 else 32700 + zone
        try:
            projected = zdf.to_crs(f"EPSG:{utm_epsg}")
            projected_centers = projected.geometry.centroid
            centers_geo = projected_centers.to_crs(4326)
        except (ValueError, RuntimeError):
            centers_geo = zdf.geometry.centroid
    else:
        projected_centers = zdf.geometry.centroid
        centers_geo = gpd.GeoSeries(projected_centers, crs=center_crs).to_crs(4326)
    zdf = zdf.to_crs(4326)
    polygons = tuple(zdf.geometry.tolist())
    xy = np.column_stack([centers_geo.x.to_numpy(), centers_geo.y.to_numpy()])
    minx, miny, maxx, maxy = zdf.total_bounds
    zones = Zones(
        ids=ids,
        polygons=polygons,
        xy=xy,
        bounds=(float(minx), float(miny), float(maxx), float(maxy)),
    )
    if "production" in zdf.columns:
        production = zdf.production.to_numpy(dtype=np.float64)
    elif "population" in zdf.columns:
        production = zdf.population.to_numpy(dtype=np.float64)
    else:
        production = np.ones(len(ids), dtype=np.float64)
    if "attraction" in zdf.columns:
        attraction = zdf.attraction.to_numpy(dtype=np.float64)
    elif "jobs" in zdf.columns:
        attraction = zdf.jobs.to_numpy(dtype=np.float64)
    else:
        attraction = production.copy()
    return zones, production, attraction


def load_od_from_files(
    matrix_path: str | Path,
    zones_path: str | Path,
    *,
    reporter: Any = None,
) -> OdResult:
    """Загружает готовую OD-матрицу и зоны из файлов (собранные внешней
    транспортной моделью), минуя расчёт на лету.

    Ожидаемый формат: ``od_matrix.parquet`` (строки/столбцы — идентификаторы
    зон) и ``zones.parquet`` (GeoDataFrame с колонкой ``zone_id`` и геометрией).
    Если ``od_matrix.parquet`` отсутствует, матрица восстанавливается из
    ``od_pairs.parquet`` (``orig_zone``/``dest_zone``/``trips``) — формат,
    получаемый при ``write_matrix_frame=False``.
    Геометрия зон перепроецируется в EPSG:4326, матрица выравнивается по
    порядку ``zone_id`` из файла зон.
    """
    import pandas as pd

    line = getattr(reporter, "line", None)
    if line:
        line(f"  Загрузка OD из файлов: {matrix_path}, {zones_path}")
    zones, weights, _attraction = load_zones_from_file(zones_path, reporter=None)
    ids = zones.ids
    ids_list = [str(int(v)) for v in ids]  # parquet может хранить id как строки
    matrix_path = Path(matrix_path)
    if matrix_path.exists():
        probe = _read_frame(matrix_path)
        if {"orig_zone", "dest_zone", "trips"}.issubset(probe.columns):
            # Файл пар: плотной матрицы нет — восстанавливаем из пар.
            mdf = _frame_from_pairs(matrix_path, ids_list)
        else:
            mdf = probe
            mdf.columns = [str(c) for c in mdf.columns]
            if not isinstance(mdf.index, pd.RangeIndex) or len(mdf) != len(ids):
                mdf.index = [str(v) for v in mdf.index]
                mdf = mdf.reindex(index=ids_list, columns=ids_list)
    else:
        if line:
            line("  Плотная матрица не найдена: восстанавливаю из OD-пар")
        mdf = _frame_from_pairs(matrix_path, ids_list)
    matrix = mdf.to_numpy(dtype=np.float64)
    if matrix.shape != (len(ids), len(ids)):
        raise OdMatrixError(
            f"Несовпадение размеров: зон в файле {len(ids)}, матрица {matrix.shape}"
        )
    if not np.isfinite(matrix).all():
        raise OdMatrixError(
            "Матрица OD не покрывает все зоны: есть NaN после выравнивания по zone_id"
        )
    n = len(ids)
    if line:
        line(
            f"  Зон: {n:,}, поездок: {float(matrix.sum()):,.0f}, "
            f"OD-пар: {int((matrix > 0).sum()):,}"
        )
    return OdResult(
        zones=zones,
        matrix=matrix,
        costs=np.zeros((n, n), dtype=np.float64),
        weights=weights,
        road_ways=0,
        euclidean=True,
        road_loads=None,
        sparse_matrix=_as_sparse(matrix),
    )


def _frame_from_pairs(
    pairs_path: str | Path, ids: Sequence[str]
) -> Any:
    """Собирает квадратную матрицу по зонам из OD-пар (без плотного parquet)."""
    import pandas as pd

    df = _read_frame(pairs_path)
    if not {"orig_zone", "dest_zone", "trips"}.issubset(df.columns):
        raise OdMatrixError(
            f"{pairs_path}: ожидаются колонки orig_zone/dest_zone/trips"
        )
    frame = pd.DataFrame(0.0, index=ids, columns=ids, dtype=float)
    keep = df["orig_zone"].astype(str).isin(ids) & df["dest_zone"].astype(str).isin(ids)
    sub = df.loc[keep, ["orig_zone", "dest_zone", "trips"]]
    if sub.empty:
        return frame
    pv = sub.pivot_table(
        index="orig_zone", columns="dest_zone", values="trips", aggfunc="sum"
    ).astype(float)
    pv = pv.fillna(0.0)  # пара даёт нуль на диагонали и в отсечённых клетках
    frame.loc[pv.index.astype(str), pv.columns.astype(str)] = pv.to_numpy()
    return frame


_OD_GENERATED_KIND = "od_generated"
# Версия формата кэша: при изменении структуры/формул пересчёт заново.
_OD_CACHE_SCHEMA_VERSION = 3


def _od_generated_cache_dir(
    cache: JsonCache | None,
    *,
    city_slug: str,
    boundary: Any,
    config: Any,
    zones_file: str | Path | None,
) -> Path | None:
    """Определяет каталог кэша сгенерированной OD-матрицы (или None)."""
    import hashlib

    if cache is None:
        return None
    weight_paths = (config.ghs_file, config.ghs_s_file)
    if not any(weight_paths):
        from .config import DEFAULT_GHS_FILE

        candidate = str(DEFAULT_GHS_FILE) if DEFAULT_GHS_FILE else None
        if candidate:
            weight_paths = (candidate, None)
    parts = [
        city_slug,
        f"v{_OD_CACHE_SCHEMA_VERSION}",
        f"zm{float(config.od_zone_size_m):.3f}",
        f"decay{getattr(config, 'od_decay_minutes', None)!s}",
        f"purposes{int(bool(getattr(config, 'od_purposes', False)))}",
        str(zones_file or ""),
        "|".join(str(p or "") for p in weight_paths),
    ]
    if boundary is not None:
        parts.append(str(boundary.wkt if hasattr(boundary, "wkt") else boundary))
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
    return Path(cache.root) / _OD_GENERATED_KIND / f"{city_slug}_{digest}"


def _save_generated_od(
    cache_dir: Path,
    *,
    zones: Zones,
    matrix: np.ndarray,
    costs: np.ndarray,
    production: np.ndarray,
    attraction: np.ndarray,
    road_ways: int,
    euclidean: bool,
    purpose_meta: dict[str, Any] | None = None,
    district_names: Sequence[str] | None = None,
) -> None:
    """Сохраняет результат генерации в кэш (parquet + npy + meta).

    Пишет в одноразовый каталог-сосед и атомарно переименовывает, чтобы
    частично записанный кэш не читался как готовый.
    """
    import os
    import shutil

    import geopandas as gpd
    import pandas as pd

    parent = cache_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f"{cache_dir.name}.", dir=parent))
    try:
        zdf = gpd.GeoDataFrame(
            {"zone_id": zones.ids, "geometry": list(zones.polygons)},
            crs="EPSG:4326",
        )
        zdf["production"] = np.asarray(production, dtype=np.float64)
        zdf["attraction"] = np.asarray(attraction, dtype=np.float64)
        if district_names is not None:
            zdf["district"] = list(district_names)
        _write_frame(zdf, tmp / "zones.parquet")
        ids = zones.ids.tolist()
        _write_frame(
            pd.DataFrame(matrix, index=ids, columns=ids),
            tmp / "matrix.parquet",
        )
        np.save(tmp / "costs.npy", costs)
        meta: dict[str, Any] = {
            "road_ways": int(road_ways),
            "euclidean": bool(euclidean),
            "schema_version": _OD_CACHE_SCHEMA_VERSION,
        }
        if purpose_meta:
            meta["purposes"] = purpose_meta
        if district_names is not None:
            meta["districts"] = list(district_names)
        (tmp / "meta.json").write_text(
            json.dumps(meta),
            encoding="utf-8",
        )
        if cache_dir.exists():
            shutil.rmtree(cache_dir, ignore_errors=True)
        os.replace(tmp, cache_dir)
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)


def _load_generated_od(
    cache_dir: Path,
    *,
    reporter: Any = None,
) -> OdResult | None:
    """Загружает OD-результат из кэша; None при повреждении/отсутствии.

    Учитывает CSV-фолбэк ``_write_frame`` (``*.parquet`` или ``*.csv``)
    и несовпадение версии формата (``schema_version``).
    """
    line = getattr(reporter, "line", None)

    def _any(*names: str) -> Path | None:
        for name in names:
            path = cache_dir / name
            if path.is_file():
                return path
        return None

    matrix_path = _any("matrix.parquet", "matrix.csv")
    zones_path = _any("zones.parquet", "zones.csv")
    costs_path = _any("costs.npy")
    meta_path = _any("meta.json")
    if not all((matrix_path, zones_path, costs_path, meta_path)):
        return None
    try:
        zones, production, _attraction = load_zones_from_file(
            zones_path, reporter=None
        )
        ids = zones.ids
        ids_list = [str(int(v)) for v in ids]
        mdf = _read_frame(matrix_path)
        mdf.index = [str(v) for v in mdf.index]
        mdf.columns = [str(c) for c in mdf.columns]
        mdf = mdf.reindex(index=ids_list, columns=ids_list)
        matrix = mdf.to_numpy(dtype=np.float64)
        costs = np.load(costs_path, allow_pickle=False)
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("schema_version") != _OD_CACHE_SCHEMA_VERSION:
            if line:
                line(
                    f"  Кэш OD устарел (версия {meta.get('schema_version')}), "
                    "пересчёт..."
                )
            return None
        purpose_periods: tuple[Period, ...] = ()
        purpose_trips: tuple[float, ...] = ()
        purpose_keys: tuple[str, ...] = ()
        purpose_meta = meta.get("purposes")
        if isinstance(purpose_meta, dict) and purpose_meta.get("out") and purpose_meta.get("ret"):
            purpose_periods = periods_from_purpose_blend(
                purpose_meta["out"], purpose_meta["ret"]
            )
            purpose_trips = tuple(float(v) for v in purpose_meta.get("totals", ()))
            purpose_keys = tuple(str(v) for v in purpose_meta.get("keys", ()))
        district_names: tuple[str, ...] = tuple(
            str(v) for v in meta.get("districts", ())
        )
        if len(district_names) != len(zones):
            district_names = ()
    except Exception as exc:  # noqa: BLE001 — повреждённый кэш не критичен
        if line:
            line(f"  Кэш OD повреждён, пересчёт: {exc}")
        return None
    return OdResult(
        zones=zones,
        matrix=matrix,
        costs=costs,
        weights=production,
        road_ways=int(meta.get("road_ways", 0)),
        euclidean=bool(meta.get("euclidean", False)),
        sparse_matrix=_as_sparse(matrix),
        purpose_periods=purpose_periods,
        purpose_trips=purpose_trips,
        purpose_keys=purpose_keys,
        district_names=district_names,
    )


def _prepare_generation(
    boundary: Any,
    config: Any,
    reporter: Any,
) -> tuple[Zones, np.ndarray, np.ndarray, np.ndarray, Any]:
    """Подготавливает зоны и веса для генерации OD.

    Возвращает ``(zones, production, attraction, weights, geo_boundary)``:
    зоны берутся из файла Tranmodel (``od_zones_file``) либо строятся по
    границе города; geo_boundary — область загрузки дорог (граница или bbox
    зон).
    """
    if getattr(config, "od_zones_file", None):
        zones, production, attraction = load_zones_from_file(
            config.od_zones_file, reporter=reporter
        )
        weights = production
        bbox = box(*zones.bounds)
        geo_boundary = boundary if boundary is not None else bbox
    else:
        if boundary is None:
            raise OdMatrixError(
                "Нет границы города и не задан --od-zones-file: "
                "не из чего строить зоны OD-матрицы"
            )
        zones = build_zones(boundary, size_m=config.od_zone_size_m)
        weight_path = next((p for p in (config.ghs_file, config.ghs_s_file) if p), None)
        if not weight_path:
            from .config import DEFAULT_GHS_FILE

            candidate = str(DEFAULT_GHS_FILE) if DEFAULT_GHS_FILE else None
            if candidate:
                weight_path = candidate
        production = zonal_weights(zones, weight_path)
        attraction = production.copy()
        weights = production
        geo_boundary = boundary
    return zones, production, attraction, weights, geo_boundary


def run_od_stage(
    *,
    boundary: Any,
    config: Any,
    session: Any,
    cache: JsonCache,
    city_slug: str,
    reporter: Any,
    out_dir: str | Path | None = None,
) -> OdResult | None:
    """Выполняет стадию OD-матрицы; None, если стадия отключена."""
    if not config.od_matrix:
        return None
    reporter.line("\n[OD] Матрица корреспонденций...")
    external_matrix = getattr(config, "od_matrix_file", None)
    if external_matrix:
        zones_file = getattr(config, "od_zones_file", None)
        if not zones_file:
            raise OdMatrixError(
                "Задана готовая матрица --od-matrix-file, но не задан "
                "--od-zones-file: зоны нужны для выравнивания матрицы"
            )
        return load_od_from_files(
            external_matrix,
            zones_file,
            reporter=reporter,
        )
    demand_file = getattr(config, "od_demand_file", None)
    if demand_file:
        demand = load_takt_demand(demand_file)
        district_names: tuple[str, ...] = ()
        districts_file = getattr(config, "od_districts_file", None)
        if districts_file:
            district_names = assign_district_names(
                demand.zones, load_districts(districts_file)
            )
        total_trips = float(demand.matrix.sum())
        n_pairs = int((demand.matrix > 0).sum())
        reporter.line(
            f"  Спрос Takt: зон {len(demand.zones):,}, поездок "
            f"{total_trips:,.0f}, OD-пар {n_pairs:,} ({Path(demand_file).name})"
        )
        save_od_outputs(
            demand.zones,
            demand.matrix,
            city=city_slug,
            out_dir=out_dir,
            production=demand.production,
            attraction=demand.production,
            district_names=district_names or None,
        )
        return OdResult(
            zones=demand.zones,
            matrix=demand.matrix,
            costs=euclidean_costs(demand.zones),
            weights=demand.production,
            road_ways=0,
            euclidean=True,
            sparse_matrix=_as_sparse(demand.matrix),
            district_names=district_names,
        )
    zones, production, attraction, weights, geo_boundary = _prepare_generation(
        boundary, config, reporter
    )
    weights_file = getattr(config, "od_weights_file", None)
    if weights_file:
        street_edges = load_demand_streets(weights_file)
        production = zone_weights_from_streets(zones, street_edges)
        attraction = production.copy()
        weights = production
        if out_dir is not None:
            _write_demand_street_geojson(
                zones, street_edges, Path(out_dir) / f"{city_slug}_od_street_demand.geojson"
            )
        reporter.line(
            f"  Веса зон: по рёбрам спроса {Path(weights_file).name} "
            f"({len(street_edges):,} рёбер)"
        )
    district_names: tuple[str, ...] = ()
    districts_file = getattr(config, "od_districts_file", None)
    if districts_file:
        places = load_districts(districts_file)
        district_names = assign_district_names(zones, places)
    cache_dir = _od_generated_cache_dir(
        cache,
        city_slug=city_slug,
        boundary=geo_boundary,
        config=config,
        zones_file=getattr(config, "od_zones_file", None),
    )
    if cache_dir is not None and getattr(cache, "read_enabled", True):
        cached = _load_generated_od(cache_dir, reporter=reporter)
        if cached is not None:
            save_od_outputs(
                cached.zones,
                cached.matrix,
                city=city_slug,
                out_dir=out_dir,
                production=production,
                attraction=attraction,
                purpose_trips=cached.purpose_trips or None,
                purpose_keys=cached.purpose_keys or None,
                district_names=(cached.district_names or None)
                if districts_file
                else None,
            )
            n_pairs = int((cached.matrix > 0).sum())
            reporter.line(
                f"  Зон: {len(cached.zones)}, поездок: {float(cached.matrix.sum()):,.0f}, "
                f"OD-пар: {n_pairs:,} (из кэша)"
            )
            return cached
    ways = fetch_road_ways(geo_boundary, session, cache, cache_key=city_slug, config=config)
    euclidean = ways is None
    if euclidean:
        reporter.line("  Дорожная сеть недоступна — время по прямой")
        costs = euclidean_costs(zones)
    else:
        costs = zone_network_costs(zones, build_road_graph(ways))
    purpose_meta: dict[str, Any] | None = None
    purpose_periods: tuple[Period, ...] = ()
    purpose_trips: list[float] | None = None
    purpose_keys: list[str] | None = None
    if getattr(config, "od_purposes", False):
        purpose_od = build_purpose_od(
            production,
            zones,
            engine=getattr(config, "od_purpose_engine", "takt"),
        )
        matrix = purpose_od.matrix
        purpose_periods = periods_from_purpose_blend(
            purpose_od.period_out, purpose_od.period_ret
        )
        purpose_trips = [float(m.sum()) for m in purpose_od.purpose_matrices]
        purpose_keys = [p.key for p in purpose_od.purposes]
        purpose_meta = {
            "out": list(purpose_od.period_out),
            "ret": list(purpose_od.period_ret),
            "totals": purpose_trips,
            "keys": purpose_keys,
        }
        reporter.line(
            "  Цели поездок: од по тяготению по целям "
            + ", ".join(f"{k}={v:,.0f}" for k, v in zip(purpose_keys, purpose_trips))
        )
    else:
        matrix = build_gravity_od(
            production,
            costs,
            attraction=attraction,
            decay_minutes=getattr(config, "od_decay_minutes", None),
        )
    if cache_dir is not None and getattr(cache, "write_enabled", True):
        _save_generated_od(
            cache_dir,
            zones=zones,
            matrix=matrix,
            costs=costs,
            production=production,
            attraction=attraction,
            road_ways=len(ways or []),
            euclidean=euclidean,
            purpose_meta=purpose_meta,
            district_names=district_names if districts_file else None,
        )
    save_od_outputs(
        zones,
        matrix,
        city=city_slug,
        out_dir=out_dir,
        production=production,
        attraction=attraction,
        purpose_trips=purpose_trips,
        purpose_keys=purpose_keys,
        district_names=district_names if districts_file else None,
    )
    total = max(float(matrix.sum()), 1.0)
    ordered = np.sort(matrix, axis=None)
    compact = ordered[ordered > 0]
    avg_time = (
        float((matrix * np.where(np.isfinite(costs), costs, 0.0)).sum() / total)
        if compact.size > 1
        else 0.0
    )
    n_pairs = int((matrix > 0).sum())
    reporter.line(
        f"  Зон: {len(zones)}, поездок: {float(matrix.sum()):,.0f}, "
        f"OD-пар: {n_pairs:,}, среднее время: {avg_time:.1f} мин "
    )
    return OdResult(
        zones=zones,
        matrix=matrix,
        costs=costs,
        weights=weights,
        road_ways=len(ways or []),
        euclidean=euclidean,
        sparse_matrix=_as_sparse(matrix),
        purpose_periods=purpose_periods,
        district_names=district_names if districts_file else (),
    )
