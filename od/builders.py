"""Построение матриц OD единым Takt-движком.

Используется алгоритм движка Takt (``xn``): сетка аттракторов, экспоненциальное
затухание по расстоянию, целочисленные поездки без балансировки и профили
периодов суток по целям поездок.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import numpy as np
from scipy import sparse

from ..passenger_flow.base.takt import TAKT_PERIODS as _TAKT_PERIODS_SLOTS
from ..passenger_flow.models import PURPOSE_DEFAULTS, Period, Purpose
from ..passenger_flow.takt import (
    _TAKT_M_PER_DEG_LAT,
    _TAKT_M_PER_DEG_LON_EQUATOR,
)
from .model import (
    OdMatrixError,
    PurposeOd,
    TaktDemand,
    TaktPurposes,
    Zones,
    _round_half_up,
)

__all__ = [
    "build_purpose_od",
    "build_takt_demand_with_purposes",
    "build_takt_od",
    "periods_from_purpose_blend",
]


_TAKT_OD_GRID_LON_DEG = 0.01
_TAKT_OD_GRID_LAT_RATIO = 0.62
_TAKT_OD_POP_CUTOFF = 40.0
_TAKT_OD_MIN_DIST_M = 50.0
_TAKT_OD_CIRCUITY = 1.35
_TAKT_OD_SPEED_MPS = 7.5
_TAKT_OD_BASE_SECONDS = 240.0

_DEFAULT_PURPOSE_PERIOD_SLOTS: tuple[tuple[str, str], ...] = (
    ("early", "04–06"),
    ("am", "06–09"),
    ("mid", "09–15"),
    ("pm", "15–19"),
    ("eve", "19–24"),
)


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
    g_e = np.fromiter((float(g[0]) for g in groups), dtype=np.float64, count=len(groups))
    g_best = np.fromiter(
        (int(g[1]) for g in groups), dtype=np.int64, count=len(groups)
    )
    g_lon = np.fromiter(
        (float(g[3]) for g in groups), dtype=np.float64, count=len(groups)
    )
    g_lat = np.fromiter(
        (float(g[4]) for g in groups), dtype=np.float64, count=len(groups)
    )
    out: list[list[int]] = []
    for origin in range(len(pts)):
        if pts[origin, 2] < _TAKT_OD_POP_CUTOFF:
            continue
        total_trips = pts[origin, 2] * float(trips_per_res)
        dist = np.hypot(
            (g_lon - pts[origin, 0]) * longitude_m_per_degree,
            (g_lat - pts[origin, 1]) * latitude_m_per_degree,
        )
        alive = (dist >= _TAKT_OD_MIN_DIST_M) & (dist <= h) & (g_best != origin)
        if not np.any(alive):
            continue
        ev = g_e[alive] * np.exp(-dist[alive] / d0_m)
        if not np.any(ev > 0.0):
            continue
        dv = dist[alive]
        bv = g_best[alive]
        band = np.searchsorted(p, dv, side="right") - 1
        total_e = float(ev.sum())
        for b in range(n_bands):
            sel = band == b
            if not np.any(sel):
                continue
            s_ev = ev[sel]
            s_dv = dv[sel]
            s_bv = bv[sel]
            band_e = float(s_ev.sum())
            if band_e <= 0.0:
                continue
            order = np.argsort(-s_ev, kind="stable")[:w]
            ranked_e = float(s_ev[order].sum())
            trips = total_trips * band_e / total_e
            carry = 0.0
            for pos in order:
                dest = int(s_bv[pos])
                e_k = float(s_ev[pos])
                dist_k = float(s_dv[pos])
                value = trips * e_k / ranked_e + carry
                n_trips = _round_half_up(value)
                carry = value - n_trips
                if n_trips > 0:
                    time_s = _round_half_up(
                        dist_k * _TAKT_OD_CIRCUITY / _TAKT_OD_SPEED_MPS
                        + _TAKT_OD_BASE_SECONDS
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
) -> PurposeOd:
    """OD по целям поездок единым Takt-алгоритмом.

    Для каждой цели используется ``build_takt_od`` с сеткой аттракторов,
    экспоненциальным затуханием и целочисленным распределением без Фёрнесса.
    Матрицы целей суммируются; профили периодов суток взвешиваются по долям
    поездок целей.
    """
    prod = np.asarray(production, dtype=np.float64)
    if prod.shape != (len(zones),):
        raise OdMatrixError("production не совпадает с числом зон")
    if prod.sum() <= 0.0:
        raise OdMatrixError("Сумма весов зон равна нулю")
    points = np.column_stack((zones.xy, prod))

    matrices: list[Any] = []
    totals: list[float] = []
    used: list[Purpose] = []
    base_times: list[np.ndarray | None] = []
    for purpose in purposes:
        prod_p = prod * float(purpose.trips_per_res)
        if prod_p.sum() <= 0.0:
            continue
        pairs = build_takt_od(
            points,
            prod,
            trips_per_res=float(purpose.trips_per_res),
            d0_m=float(purpose.d0_m),
            k=purpose.k,
        )
        n = len(zones)
        if len(pairs):
            matrix_p = sparse.coo_matrix(
                (
                    pairs[:, 2].astype(np.float64),
                    (pairs[:, 0], pairs[:, 1]),
                ),
                shape=(n, n),
            ).tocsr()
            rows_p, cols_p = matrix_p.nonzero()
            time_weighted = sparse.coo_matrix(
                (
                    pairs[:, 2].astype(np.float64) * pairs[:, 3].astype(np.float64),
                    (pairs[:, 0], pairs[:, 1]),
                ),
                shape=(n, n),
            ).tocsr()
            trips_p = np.asarray(matrix_p[rows_p, cols_p]).ravel()
            weighted_p = np.asarray(time_weighted[rows_p, cols_p]).ravel()
            pair_base = np.divide(
                weighted_p,
                trips_p,
                out=np.zeros_like(weighted_p),
                where=trips_p > 0.0,
            )
            base_times.append(
                np.repeat(pair_base[None, :], len(_TAKT_PERIODS_SLOTS), axis=0)
            )
        else:
            matrix_p = sparse.csr_matrix((n, n), dtype=np.float64)
            base_times.append(
                np.zeros((len(_TAKT_PERIODS_SLOTS), 0), dtype=np.float64)
            )
        matrices.append(matrix_p)
        used.append(purpose)
        totals.append(float(matrix_p.sum()))
    if not matrices:
        raise OdMatrixError("Ни одна цель не дала поездок")
    matrix = np.zeros((len(zones), len(zones)), dtype=np.float64)
    for mat in matrices:
        matrix += mat.toarray() if sparse.issparse(mat) else mat
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
        purpose_base_times=tuple(base_times),
    )


def build_takt_demand_with_purposes(
    demand: TaktDemand,
    purposes: TaktPurposes,
) -> tuple[np.ndarray, tuple[float, ...], tuple[float, ...], tuple[str, ...], tuple[float, ...]]:
    """Собирает матрицу как движок Takt: спрос + слои целей.

    Движок строит ``m = [{od: спрос, out/ret: глобальные периоды}, ...слои
    целей]``; поездки слоёв складываются, а профили периодов суток
    взвешиваются долями поездок слоёв (глобальные периоды для спроса, свои
    ``out``/``ret`` для каждой цели). Возвращает
    ``(matrix, period_out, period_ret, keys, layer_trips)``.
    """
    n = len(demand.zones)
    matrix = demand.matrix.copy()
    demand_trips = float(matrix.sum())
    keys: list[str] = []
    layer_trips: list[float] = []
    layers_tbl: list[tuple[tuple[float, ...], tuple[float, ...]] | None] = []
    if demand_trips > 0.0:
        keys.append("demand")
        layer_trips.append(demand_trips)
        layers_tbl.append(
            (tuple(p.out for p in _TAKT_PERIODS_SLOTS),
             tuple(p.ret for p in _TAKT_PERIODS_SLOTS))
        )
    for layer in purposes.layers:
        pairs = layer.pairs
        if pairs.shape[0] == 0:
            continue
        trips = float(pairs[:, 2].sum())
        if trips <= 0.0:
            continue
        if pairs[:, :2].min() < 0 or pairs[:, :2].max() >= n:
            raise OdMatrixError(
                f"Файл целей Takt: индекс точки вне диапазона слоя {layer.key!r}"
            )
        np.add.at(
            matrix,
            (pairs[:, 0].astype(int), pairs[:, 1].astype(int)),
            pairs[:, 2],
        )
        keys.append(layer.key)
        layer_trips.append(trips)
        layers_tbl.append((layer.out, layer.ret))
    total_trips = max(float(sum(layer_trips)), 1.0)
    out_weights = np.zeros(len(_TAKT_PERIODS_SLOTS), dtype=np.float64)
    ret_weights = np.zeros(len(_TAKT_PERIODS_SLOTS), dtype=np.float64)
    for trips, tbl in zip(layer_trips, layers_tbl):
        share = trips / total_trips
        if not tbl:
            continue
        period_out, period_ret = tbl
        for i in range(min(len(period_out), len(out_weights))):
            out_weights[i] += float(period_out[i]) * share
            ret_weights[i] += float(period_ret[i]) * share
    return (
        matrix,
        tuple(float(v) for v in out_weights),
        tuple(float(v) for v in ret_weights),
        tuple(keys),
        tuple(layer_trips),
    )