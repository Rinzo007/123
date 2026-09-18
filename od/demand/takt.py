"""Спрос OD: готовые данные Takt и веса зон по рёбрам спроса.

Чтение ``demand.json`` (точки + OD-пары) и ``demand-streets.json``
(FeatureCollection рёбер спроса), раскладка весов рёбер по зонам
пропорционально длине попавших в зону участков.
"""

from __future__ import annotations

import itertools
import json
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
from scipy.spatial import cKDTree
from shapely.geometry import box

from ..model import (
    _KILOMETERS_PER_DEGREE,
    OdMatrixError,
    TaktDemand,
    Zones,
    _cosscale,
    haversine_km,
)

__all__ = [
    "load_demand_streets",
    "load_takt_demand",
    "zone_weights_from_streets",
]

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