"""Waiting time: headway expectation, reliability and MSA loop.

Extracted from `passenger_flow/core.py`: wait formulas (`linear` /
`takt`), reliability penalty and iterated assignment with method of
successive averages.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from od.model import Zones
else:
    Zones = Any
from ..base.models import ModeChoiceConfig, VehicleSpec, vehicle_spec_for_route_type
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

def _build_crowd_state(
    route_sequences: list[dict[str, Any]],
    seg_forward: Mapping[tuple[int, int], float],
    seg_reverse: Mapping[tuple[int, int], float],
    seq_stop_totals: Mapping[tuple[int, int], float],
    seq_headway_min: Mapping[int, float] | None,
    vehicle_specs: Mapping[str, VehicleSpec] | None,
    period_hours: float,
    period_index: int = 0,
) -> dict[str, dict[tuple[int, int], float]]:
    """Строит segment/stop feedback в том же пространстве, что JS Fr()."""
    state: dict[str, Any] = {
        "seg_forward": {},
        "seg_reverse": {},
        "seg_forward_prefix": {},
        "seg_reverse_prefix": {},
        "stop_extra": {},
        "unreliability": {},
    }
    if seq_headway_min is None:
        return state

    for seq_idx, seq in enumerate(route_sequences):
        h = float(seq_headway_min.get(seq_idx, 0.0))
        if h <= 0.0:
            continue
        spec = vehicle_specs.get(str(seq.get("route_type_key") or "").lower()) if vehicle_specs else None
        if spec is None:
            spec = vehicle_spec_for_route_type(seq.get("route_type_key", "bus"))
        period_runs = period_hours * 60.0 / h
        if period_runs <= 0.0 or spec.capacity <= 0:
            continue

        capacity = float(seq.get("capacity") or spec.capacity)
        denom = period_runs * max(capacity, 1.0)
        seg_count = (
            len(seq["stops"])
            if seq.get("closed")
            else max(0, len(seq["stops"]) - 1)
        )
        for seg_idx in range(seg_count):
            f = float(seg_forward.get((seq_idx, seg_idx), 0.0))
            r = float(seg_reverse.get((seq_idx, seg_idx), 0.0))
            state["seg_forward"][(seq_idx, seg_idx)] = max(
                f / denom, 0.0
            )
            state["seg_reverse"][(seq_idx, seg_idx)] = max(
                r / denom, 0.0
            )

        segment_time = seq.get("segment_time_s") or ()
        forward_prefix = [0.0]
        reverse_prefix = [0.0]
        for seg_idx in range(seg_count):
            f_load = float(state["seg_forward"].get((seq_idx, seg_idx), 0.0))
            r_load = float(state["seg_reverse"].get((seq_idx, seg_idx), 0.0))
            seg_time_s = (
                float(segment_time[seg_idx])
                if seg_idx < len(segment_time)
                else 0.0
            )
            f_extra = (
                seg_time_s * (_takt_crowding_ride_mult(f_load) - 1.0)
                if f_load > 0.0
                else 0.0
            )
            r_extra = (
                seg_time_s * (_takt_crowding_ride_mult(r_load) - 1.0)
                if r_load > 0.0
                else 0.0
            )
            forward_prefix.append(forward_prefix[-1] + f_extra)
            reverse_prefix.append(reverse_prefix[-1] + r_extra)
        state["seg_forward_prefix"][seq_idx] = tuple(forward_prefix)
        state["seg_reverse_prefix"][seq_idx] = tuple(reverse_prefix)

        direction_factor = (
            2.0 if not seq.get("closed") or seq.get("both_ways") else 1.0
        )
        dwell_integral = 0.0
        open_values = seq.get("open")
        for stop_idx in range(len(seq["stops"])):
            pax = float(seq_stop_totals.get((seq_idx, stop_idx), 0.0))
            if pax <= 0.0:
                continue
            if open_values is not None and not bool(open_values[stop_idx]):
                continue
            dwell_integral += (
                float(spec.dwell_per_pax_s)
                * pax
                / (
                    2.0
                    * direction_factor
                    * max(float(period_hours), 1e-9)
                    * 3600.0
                )
            )
        H = min(
            1.0,
            float(spec.jitter_s) * math.exp(dwell_integral) / (h * 60.0),
        )
        state["unreliability"][(seq_idx, period_index)] = 1.0 + H * H
        dwell_runs = direction_factor * period_runs
        if dwell_runs > 0.0:
            for stop_idx in range(len(seq["stops"])):
                pax = float(seq_stop_totals.get((seq_idx, stop_idx), 0.0))
                if pax <= 0.0:
                    continue
                state["stop_extra"][(seq_idx, stop_idx)] = (
                    float(spec.dwell_per_pax_s) * pax / dwell_runs
                )

    return state


def _takt_msa_gap(
    previous_route: Mapping[int, float],
    current_route: Mapping[int, float],
    previous_seg_forward: Mapping[tuple[int, int], float],
    current_seg_forward: Mapping[tuple[int, int], float],
    previous_seg_reverse: Mapping[tuple[int, int], float],
    current_seg_reverse: Mapping[tuple[int, int], float],
    previous_stop: Mapping[tuple[int, int], float],
    current_stop: Mapping[tuple[int, int], float],
) -> float:
    """Takt MSA gap: сумма абсолютных шагов / сумма текущих нагрузок."""
    def step(a: Mapping[Any, float], b: Mapping[Any, float]) -> float:
        keys = set(a) | set(b)
        return sum(abs(float(b.get(key, 0.0)) - float(a.get(key, 0.0))) for key in keys)

    # Takt Os/$s compares only segment and stop loads. Route totals are
    # not part of K/tt in the bundle and must not affect the MSA stopping test.
    numerator = (
        step(previous_seg_forward, current_seg_forward)
        + step(previous_seg_reverse, current_seg_reverse)
        + step(previous_stop, current_stop)
    )
    denominator = max(
        sum(float(v) for v in current_seg_forward.values())
        + sum(float(v) for v in current_seg_reverse.values())
        + sum(float(v) for v in current_stop.values()),
        1.0,
    )
    return numerator / denominator

class _MsaLoadVector:
    """Плотное MSA-хранилище для фиксированного набора (sequence, index)."""

    __slots__ = ("keys", "index", "values", "_scratch")

    def __init__(self, keys: tuple[tuple[int, int], ...]) -> None:
        self.keys = keys
        self.index = {key: i for i, key in enumerate(keys)}
        self.values = np.zeros(len(keys), dtype=np.float64)
        self._scratch = np.zeros(len(keys), dtype=np.float64)

    def get(self, key: tuple[int, int], default: float = 0.0) -> float:
        pos = self.index.get(key)
        return float(self.values[pos]) if pos is not None else default

    def items(self):
        for key, value in zip(self.keys, self.values):
            yield key, float(value)

    def as_dict(self) -> dict[tuple[int, int], float]:
        return {
            key: float(value)
            for key, value in zip(self.keys, self.values)
            if float(value) != 0.0
        }


def _build_msa_load_vector(
    route_sequences: list[dict[str, Any]],
    *,
    stops: bool,
) -> _MsaLoadVector:
    keys: list[tuple[int, int]] = []
    for seq_idx, seq in enumerate(route_sequences):
        count = len(seq.get("stops") or []) if stops else (
            len(seq.get("stops") or [])
            if seq.get("closed")
            else max(0, len(seq.get("stops") or []) - 1)
        )
        keys.extend((seq_idx, i) for i in range(count))
    return _MsaLoadVector(tuple(keys))


def _msa_smooth_vector(
    smoothed: _MsaLoadVector,
    raw: Mapping[tuple[int, int], float],
    alpha: float,
) -> tuple[float, float]:
    """MSA-сглаживание в numpy без построения union/set."""
    scratch = smoothed._scratch
    scratch.fill(0.0)
    for key, value in raw.items():
        pos = smoothed.index.get(key)
        if pos is not None:
            scratch[pos] = float(value)
    previous = smoothed.values.copy()
    smoothed.values *= 1.0 - alpha
    smoothed.values += alpha * scratch
    numerator = float(np.abs(smoothed.values - previous).sum())
    total = float(smoothed.values.sum())
    return numerator, total



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
    no_car_shares: np.ndarray | None = None,
    wait_calc: str = "takt",
    period_hours: float = 24.0,
    vehicle_specs: Mapping[str, VehicleSpec] | None = None,
    base_time_s: np.ndarray | None = None,
    car_base_time_s: np.ndarray | None = None,
    period_index: int = 0,
    car_period_multiplier: float = 1.0,
    transfer_index: Mapping[tuple[int, int], tuple[tuple[int, dict[str, Any], dict[str, Any]], ...]] | None = None,
    od_distances_m: np.ndarray | None = None,
    ride_edge_cache: dict[tuple[int, int, int], float] | None = None,
    perf_stats: dict[str, float] | None = None,
) -> tuple[dict[str, Any], int, float]:
    """Итеративное присваивание с методом последовательных усреднений (MSA).

    На каждой итерации стоимость поездок учитывает текущее доп. ожидание
    перегрузки, после чего её нагрузка смешивается с предыдущей шагом
    ``1 / iteration``. Остановка при относительном разрыве нагрузок
    маршрутов не больше ``gap_tol`` (по мотивам MSA-цикла Takt, gap <= 1%).
    """
    msa_started = perf_counter() if perf_stats is not None else 0.0
    smoothed_seg_forward = _build_msa_load_vector(route_sequences, stops=False)
    smoothed_seg_reverse = _build_msa_load_vector(route_sequences, stops=False)
    smoothed_stop = _build_msa_load_vector(route_sequences, stops=True)
    wait_extra = dict(reliability_extra) if reliability_extra else None
    crowd_state = None
    agg: dict[str, Any] = {}
    final_gap = gap_tol
    if ride_edge_cache is None:
        ride_edge_cache = {}
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
            no_car_shares=no_car_shares,
            wait_calc=wait_calc,
            base_time_s=base_time_s,
            car_base_time_s=car_base_time_s,
            period_index=period_index,
            crowd_state=crowd_state,
            transfer_index=transfer_index,
            car_period_multiplier=car_period_multiplier,
            od_distances_m=od_distances_m,
            ride_edge_cache=ride_edge_cache,
            perf_stats=perf_stats,
        )
        alpha = 1.0 / iteration
        gap_num = 0.0
        gap_total = 0.0
        for target, source in (
            (smoothed_seg_forward, agg.get("seg_forward_totals", {})),
            (smoothed_seg_reverse, agg.get("seg_reverse_totals", {})),
            (smoothed_stop, agg.get("seq_stop_totals", {})),
        ):
            num, total = _msa_smooth_vector(target, source, alpha)
            gap_num += num
            gap_total += total
        crowd_started = perf_counter() if perf_stats is not None else 0.0
        crowd_state = _build_crowd_state(
            route_sequences,
            smoothed_seg_forward,
            smoothed_seg_reverse,
            smoothed_stop,
            seq_headway_min,
            vehicle_specs,
            period_hours,
            period_index=period_index,
        )
        if perf_stats is not None:
            perf_stats.setdefault("crowd_state_s", 0.0)
            perf_stats["crowd_state_s"] += perf_counter() - crowd_started
        final_gap = gap_num / max(gap_total, 1.0)
        if iteration > 1 and final_gap <= gap_tol:
            break
        wait_extra = dict(reliability_extra) if reliability_extra else None
    agg["seg_forward_totals"] = smoothed_seg_forward.as_dict()
    agg["seg_reverse_totals"] = smoothed_seg_reverse.as_dict()
    agg["seq_stop_totals"] = smoothed_stop.as_dict()
    if perf_stats is not None:
        perf_stats.setdefault("msa_s", 0.0)
        perf_stats["msa_s"] += perf_counter() - msa_started
    return agg, iteration, final_gap
