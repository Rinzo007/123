"""Спрос OD: готовые данные Takt и веса зон по рёбрам спроса.

Чтение ``demand.json`` (точки + OD-пары), ``purposes.bin.json`` (слои целей
поездок с профилями периодов суток) и ``demand-streets.json``
(FeatureCollection рёбер спроса), раскладка весов рёбер по зонам
пропорционально длине попавших в зону участков.
"""

from __future__ import annotations

import base64
import itertools
import json
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
from scipy import sparse
from scipy.spatial import cKDTree
from shapely.geometry import box

from ..model import (
    _KILOMETERS_PER_DEGREE,
    OdMatrixError,
    TaktDemand,
    TaktPurposeLayer,
    TaktPurposes,
    Zones,
    _cosscale,
    haversine_km,
)

__all__ = [
    "load_demand_streets",
    "load_takt_demand",
    "load_takt_purposes",
    "write_takt_demand",
    "write_takt_purposes",
    "write_takt_purposes_bundle",
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


def load_takt_purposes(path: str | Path) -> TaktPurposes:
    """Читает ``purposes.bin.json`` из пакета Takt (слои целей поездок).

    Структура файла: ``{"v": 2, "layers": [...], "commuteBaseT": [...]}``,
    где каждый слой содержит ``t`` (код цели), ``n`` (число пар), ``out``/
    ``ret`` (профили периодов суток) и закодированные base64 float32-потоки:
    ``od`` — ``[origin, dest, trips, seconds]``, ``baseT`` — одномерные
    времена поездки для каждого периода. Возвращает ``TaktPurposes`` со
    слоями (индексы пар оставляются как в файле).
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "layers" not in data:
        raise OdMatrixError(f"Файл целей Takt не содержит layers: {path}")

    def _f32(b64: Any, expect: int | None, what: str) -> np.ndarray:
        if not isinstance(b64, str):
            raise OdMatrixError(f"Файл целей Takt: {what} не строка base64: {path}")
        try:
            arr = np.frombuffer(base64.b64decode(b64), dtype="<f4")
        except Exception as exc:
            raise OdMatrixError(f"Файл целей Takt: не удалось декодировать {what}: {path}") from exc
        if expect is not None and arr.size != expect:
            raise OdMatrixError(
                f"Файл целей Takt: {what} содержит {arr.size} значений, "
                f"ожидалось {expect}: {path}"
            )
        return arr.copy()

    layers: list[TaktPurposeLayer] = []
    for item in data["layers"]:
        if not isinstance(item, dict):
            raise OdMatrixError(f"Файл целей Takt: слой не объект: {path}")
        t = str(item.get("t", ""))
        n = int(item.get("n", 0))
        out = tuple(float(v) for v in item.get("out", ()))
        ret = tuple(float(v) for v in item.get("ret", ()))
        pairs = _f32(item.get("od"), n * 4, f"od слоя {t!r}").reshape(-1, 4)
        base_t = item.get("baseT")
        base_time = None
        if isinstance(base_t, list):
            base_time = np.stack(
                [_f32(p, n, f"baseT[{i}] слоя {t!r}") for i, p in enumerate(base_t)]
            )
        layers.append(
            TaktPurposeLayer(key=t, out=out, ret=ret, pairs=pairs, base_time=base_time)
        )
    commute = data.get("commuteBaseT")
    commute_base_time = None
    if isinstance(commute, list) and commute:
        decoded = [_f32(p, None, f"commuteBaseT[{i}]") for i, p in enumerate(commute)]
        if len({arr.size for arr in decoded}) == 1:
            commute_base_time = np.stack(decoded)
    return TaktPurposes(layers=tuple(layers), commute_base_time=commute_base_time)


def write_takt_demand(
    zones: Zones,
    matrix: np.ndarray,
    *,
    production: np.ndarray,
    attraction: np.ndarray,
    costs: np.ndarray,
    path: Path,
) -> None:
    """Пишет ``demand.json`` (полный спрос) в формате пакета Takt.

    Структура ``{"pts": [[lon, lat, pop, jobs], ...], "od": ...}``:
    ``pts`` — центроиды зон с производством (население) и притяжением
    (рабочие места), ``od`` — пары ненулевых поездок полной матрицы
    ``[fromPt, toPt, trips, travelTimeS]`` с индексами точек (нулевые)
    и временем поездки в секундах. Автономен: содержит полный спрос.
    """
    xy = np.asarray(zones.xy, dtype=np.float64)
    pts = np.column_stack(
        (
            xy[:, 0],
            xy[:, 1],
            np.asarray(production, dtype=np.float64),
            np.asarray(attraction, dtype=np.float64),
        )
    )
    rows, cols = np.nonzero(matrix > 0)
    keep = rows != cols
    rows, cols = rows[keep], cols[keep]
    finite = np.where(np.isfinite(costs[rows, cols]), costs[rows, cols], 0.0)
    time = (np.rint(finite * 60.0)).astype(np.int64)
    od = np.column_stack(
        (rows, cols, np.rint(matrix[rows, cols]).astype(np.int64), time)
    )
    payload = {"pts": pts.tolist(), "od": od.tolist()}
    try:
        import orjson

        raw = orjson.dumps(payload)
    except ImportError:
        raw = json.dumps(payload).encode("utf-8")
    Path(path).write_bytes(raw)


def write_takt_purposes(
    purpose_od: Any,
    costs: np.ndarray,
    path: Path,
) -> None:
    """Пишет ``purposes.bin.json`` (слои целей) в формате пакета Takt.

    Каждый слой — цель из ``purpose_od``: ``t`` (код цели), ``n`` (число
    пар), ``out``/``ret`` (профили периодов суток) и закодированные base64
    float32-потоки ``od`` — ``[origin, dest, trips, seconds]``. Сумма слоёв
    равна полной матрице (автономен).
    """
    layers: list[dict[str, Any]] = []
    for purpose_index, (purpose, mat) in enumerate(zip(purpose_od.purposes, purpose_od.purpose_matrices)):
        if sparse.issparse(mat):
            mat = mat.tocsr()
            mat.sum_duplicates()
            rows, cols = mat.nonzero()
            vals = np.asarray(mat.data, dtype=np.float64)
        else:
            rows, cols = np.nonzero(mat > 0)
            vals = np.asarray(mat[rows, cols], dtype=np.float64).ravel()
        keep = rows != cols
        rows, cols, vals = rows[keep], cols[keep], vals[keep]
        if not rows.size:
            continue
        retained_base = None
        base_items = getattr(purpose_od, "purpose_base_times", ())
        if purpose_index < len(base_items):
            retained_base = base_items[purpose_index]
        if retained_base is not None:
            retained = np.asarray(retained_base, dtype=np.float64)
            if retained.ndim != 2 or retained.shape[1] != rows.size:
                raise OdMatrixError(
                    f"Цель {purpose.key!r}: purpose_base_times не совпадает с OD-парами"
                )
            time = np.rint(retained[0]).astype(np.int64)
        else:
            time = (np.rint(costs[rows, cols] * 60.0)).astype(np.int64)
        pairs = np.column_stack(
            (rows, cols, np.rint(vals).astype(np.int64), time)
        )
        encoded = base64.b64encode(pairs.astype("<f4").tobytes()).decode("ascii")
        layer_payload: dict[str, Any] = {
            "t": purpose.key,
            "n": int(pairs.shape[0]),
            "out": [float(v) for v in purpose.out],
            "ret": [float(v) for v in purpose.ret],
            "od": encoded,
        }
        if retained_base is not None:
            retained = np.asarray(retained_base, dtype="<f4")
            layer_payload["baseT"] = [
                base64.b64encode(retained[i].tobytes()).decode("ascii")
                for i in range(retained.shape[0])
            ]
        layers.append(layer_payload)
    Path(path).write_text(
        json.dumps({"v": 2, "layers": layers, "commuteBaseT": []}),
        encoding="utf-8",
    )


def write_takt_purposes_bundle(
    purposes: TaktPurposes,
    path: Path,
) -> None:
    """Записывает ``TaktPurposes`` без потери ``baseT`` и ``commuteBaseT``.

    Это зеркальная операция к ``load_takt_purposes``: бинарные потоки
    перекодируются в base64 float32 с теми же размерами и порядком пар.
    """
    layers: list[dict[str, Any]] = []
    for layer in purposes.layers:
        pairs = np.asarray(layer.pairs, dtype="<f4")
        if pairs.ndim != 2 or pairs.shape[1] != 4:
            raise OdMatrixError(f"Слой {layer.key!r}: pairs должны иметь форму [n,4]")
        payload: dict[str, Any] = {
            "t": layer.key,
            "n": int(pairs.shape[0]),
            "out": [float(v) for v in layer.out],
            "ret": [float(v) for v in layer.ret],
            "od": base64.b64encode(pairs.tobytes()).decode("ascii"),
        }
        if layer.base_time is not None:
            base = np.asarray(layer.base_time, dtype="<f4")
            if base.ndim != 2 or base.shape[1] != pairs.shape[0]:
                raise OdMatrixError(
                    f"Слой {layer.key!r}: base_time должен иметь форму [period,n]"
                )
            payload["baseT"] = [
                base64.b64encode(base[i].tobytes()).decode("ascii")
                for i in range(base.shape[0])
            ]
        layers.append(payload)
    commute_base = None
    if purposes.commute_base_time is not None:
        commute = np.asarray(purposes.commute_base_time, dtype="<f4")
        if commute.ndim != 2:
            raise OdMatrixError("commute_base_time должен иметь форму [period,n]")
        commute_base = [
            base64.b64encode(commute[i].tobytes()).decode("ascii")
            for i in range(commute.shape[0])
        ]
    payload = {"v": 2, "layers": layers, "commuteBaseT": commute_base or []}
    Path(path).write_text(json.dumps(payload), encoding="utf-8")


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