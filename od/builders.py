"""Построение матриц OD: гравитация (Фёрнесс) и тяготение по целям.

Два независимых движка распределения поездок: классическая гравитация
``exp(-beta*t)`` с балансировкой Фёрнесс и алгоритм движка Takt (``xn``)
с целочисленными поездками без балансировки; поверх них — OD по целям
поездок с профилями периодов суток.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
from scipy import sparse

from ..passenger_flow.models import PURPOSE_DEFAULTS, Period, Purpose
from ..passenger_flow.takt import (
    _TAKT_M_PER_DEG_LAT,
    _TAKT_M_PER_DEG_LON_EQUATOR,
)
from .model import OdMatrixError, PurposeOd, Zones, _round_half_up

__all__ = [
    "build_gravity_od",
    "build_purpose_od",
    "build_takt_od",
    "periods_from_purpose_blend",
]

_DECAY_RADIUS_KM = 5.5
_GRAVITY_REF_SPEED_KMH = 25.0
_SPARSE_GRAVITY_CELLS = 3_000_000
_GRAVITY_KERNEL_EPS = 1e-4
_FURNESS_MAX_ITER = 300
_FURNESS_TOL = 1e-5

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
    # Не создаём вторую плотную матрицу экспоненты: для больших сетей
    # достаточно выбрать пары, для которых расстояние не превышает
    # эффективный радиус ядра.
    threshold = -math.log(_GRAVITY_KERNEL_EPS) / beta
    finite = np.isfinite(impedance) & (impedance <= threshold)
    finite &= ~np.eye(impedance.shape[0], dtype=bool)
    rows, cols = np.nonzero(finite)
    if rows.size == 0:
        return sparse.csr_matrix(impedance.shape, dtype=np.float64)
    values = np.exp(-beta * impedance[rows, cols])
    values *= production[rows]
    values *= attraction[cols]
    return sparse.csr_matrix((
        values,
        (rows, cols),
    ), shape=impedance.shape)


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