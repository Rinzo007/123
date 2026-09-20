"""Waiting time: headway expectation, reliability and MSA loop.

Extracted from `passenger_flow/core.py`: wait formulas (`linear` /
`takt`), reliability penalty and iterated assignment with method of
successive averages.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import numpy as np

from ...od import Zones
from ..base.models import ModeChoiceConfig
from ..base.takt import (
    _TAKT_RELIABILITY_FLOOR_S,
    _TAKT_RELIABILITY_JITTER_FACTOR,
    _TAKT_WAIT_EXTRA_PER_MIN,
    _TAKT_WAIT_FLOOR_MIN,
    _TAKT_WAIT_LINEAR_LIMIT_MIN,
)
from .assign import _assign_od


def _expected_wait_min(
    headway_min: float | None,
    wait_time_min: float,
    wait_calc: str,
) -> float:
    """Ожидание на посадке по интервалу движения (мин).

    ``linear`` (по умолчанию) — ``max(wait_time_min, headway / 2)``;
    ``takt`` — ``headway / 2`` при headway <= 12 мин, иначе
    ``6 + 0.1 × headway`` (насыщение потерь при редком движении, Takt).
    """
    if headway_min is None:
        return wait_time_min
    if wait_calc == "takt":
        if headway_min <= _TAKT_WAIT_LINEAR_LIMIT_MIN:
            return headway_min / 2.0
        return _TAKT_WAIT_FLOOR_MIN + _TAKT_WAIT_EXTRA_PER_MIN * headway_min
    return max(wait_time_min, headway_min / 2.0)

def _reliability_min(jitter_s: float, headway_min: float) -> float:
    """Штраф надёжности расписания к ожиданию (мин): max(20 с,
    hypot(jitter_s, 0.4 × headway_с)) — по модели Takt."""
    headway_s = headway_min * 60.0
    return (
        max(
            _TAKT_RELIABILITY_FLOOR_S,
            math.hypot(jitter_s, _TAKT_RELIABILITY_JITTER_FACTOR * headway_s),
        )
        / 60.0
    )

def _build_wait_extra(
    route_sequences: list[dict[str, Any]],
    route_totals: Mapping[int, float],
    wait_crowding_per_100_min: float,
    reliability_extra: Mapping[int, float] | None,
) -> dict[int, float] | None:
    """Дополнительное ожидание по маршрутам: надёжность + перегрузка
    (per-route trips → минуты через ``wait_crowding_per_100_min``)."""
    extra = dict(reliability_extra) if reliability_extra else {}
    for seq_idx, seq in enumerate(route_sequences):
        trips = route_totals.get(seq["route_id"], 0.0)
        if trips > 0.0:
            extra[seq_idx] = (
                extra.get(seq_idx, 0.0)
                + wait_crowding_per_100_min * trips / 100.0
            )
    return extra or None

def _run_msa_period(
    od_rows: np.ndarray,
    od_cols: np.ndarray,
    od_vals: np.ndarray,
    zone_nearest: Mapping[int, list[tuple[int, int, int, float]]],
    route_sequences: list[dict[str, Any]],
    *,
    stop_time_min: float,
    wait_time_min: float,
    walk_to_stop_min: float,
    transfer_penalty_min: float,
    transfer_wait_min: float | None,
    transfer_radius_m: float,
    max_transfers: int,
    transfer_penalty_calc: str,
    logit_temp: float,
    out_factor: float,
    ret_factor: float,
    mode: ModeChoiceConfig | None,
    zones: Zones,
    wait_crowding_per_100_min: float,
    reliability_extra: Mapping[int, float] | None,
    max_iterations: int,
    gap_tol: float,
    seq_headway_min: Mapping[int, float] | None = None,
    seq_jitter_s: Mapping[int, float] | None = None,
) -> tuple[dict[str, Any], int, float]:
    """Итеративное присваивание с методом последовательных усреднений (MSA).

    На каждой итерации стоимость поездок учитывает текущее доп. ожидание
    перегрузки, после чего её нагрузка смешивается с предыдущей шагом
    ``1 / iteration``. Остановка при относительном разрыве нагрузок
    маршрутов не больше ``gap_tol`` (по мотивам MSA-цикла Takt, gap <= 1%).
    """
    smoothed: dict[int, float] = {}
    prev_smoothed: dict[int, float] = {}
    smoothed_seg: dict[tuple[int, int], float] = {}
    wait_extra = dict(reliability_extra) if reliability_extra else None
    agg: dict[str, Any] = {}
    final_gap = gap_tol
    for iteration in range(1, max_iterations + 1):
        agg = _assign_od(
            od_rows,
            od_cols,
            od_vals,
            zone_nearest,
            route_sequences,
            stop_time_min=stop_time_min,
            wait_time_min=wait_time_min,
            walk_to_stop_min=walk_to_stop_min,
            transfer_penalty_min=transfer_penalty_min,
            transfer_wait_min=transfer_wait_min,
            transfer_radius_m=transfer_radius_m,
            max_transfers=max_transfers,
            transfer_penalty_calc=transfer_penalty_calc,
            logit_temp=logit_temp,
            out_factor=out_factor,
            ret_factor=ret_factor,
            wait_extra=wait_extra,
            mode=mode,
            zones=zones,
            seq_headway_min=seq_headway_min,
            seq_jitter_s=seq_jitter_s,
        )
        raw = agg["route_totals"]
        alpha = 1.0 / iteration
        rids = sorted(set(raw) | set(smoothed))
        for rid in rids:
            smoothed[rid] = (
                (1.0 - alpha) * smoothed.get(rid, 0.0)
                + alpha * raw.get(rid, 0.0)
            )
        raw_seg = agg["seg_totals"]
        seg_keys = set(raw_seg) | set(smoothed_seg)
        for key in seg_keys:
            smoothed_seg[key] = (
                (1.0 - alpha) * smoothed_seg.get(key, 0.0)
                + alpha * raw_seg.get(key, 0.0)
            )
        if smoothed_seg:
            agg["seg_totals"] = smoothed_seg
        denom = max(sum(smoothed.values()), 1.0)
        final_gap = (
            sum(
                abs(smoothed.get(r, 0.0) - prev_smoothed.get(r, 0.0))
                for r in rids
            )
            / denom
        )
        prev_smoothed = dict(smoothed)
        if iteration > 1 and final_gap <= gap_tol:
            break
        wait_extra = _build_wait_extra(
            route_sequences,
            smoothed,
            wait_crowding_per_100_min,
            reliability_extra,
        )
    return agg, iteration, final_gap
