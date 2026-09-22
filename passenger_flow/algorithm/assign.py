"""Per-OD assignment of trips to routes and mode shares.

Extracted from `passenger_flow/core.py`: one pass over all OD pairs, journey
enumeration, logit split and accumulation of route/direction/stop totals.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from time import perf_counter
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from od.model import Zones
else:
    Zones = Any
from ..base.models import ModeChoiceConfig
from ..base.takt import (
    _takt_crowding_wait_mult,
    _takt_po_seconds,
)
from ..network.geometry import _stop_key, haversine_meters
from ..network.routes import (
    _route_segment_indices,
    _scheduled_transfer_wait_min,
    _leg_alternatives,
    _cached_ride_edge_time_min,
    build_journeys,
)
from .mode_choice import (
    _od_fare_eur,
    _takt_mode_shares,
    _takt_route_choice,
    _takt_route_probs,
)


# Вариант поездки: (полное время в минутах, кортеж ножек);
# ножка — (seq_idx, позиция посадки, позиция высадки).
_Journey = tuple[float, tuple[tuple[int, int, int], ...]]


def _line_access_stops(
    candidates: list[tuple[int, int, int, float]],
    route_sequences: list[dict[str, Any]],
) -> list[tuple[int, int, int, float]]:
    """Оставляет кандидаты-остановки, попадающие в радиус доступа линии.

    Каждый кандидат зоны — ``(seq_idx, stop_idx, position, dist_m)``; линия
    обслуживает точку, только если расстояние до остановки <= её ``access_m``
    (движок Takt: ``nearD[c] <= access``).
    """
    kept: list[tuple[int, int, int, float]] = []
    for seqi, stopi, posi, dist_m in candidates:
        if dist_m <= float(route_sequences[seqi]["access_m"]):
            kept.append((seqi, stopi, posi, float(dist_m)))
    return kept


# ===== Аккумуляторы =====


def _stop_totals_entry() -> dict[str, Any]:
    """Дефолтная запись остановки для ``_OdTotals.stop_totals``."""
    return {
        "stop_id": None,
        "name": "",
        "boardings": 0.0,
        "alightings": 0.0,
        "lat": 0.0,
        "lon": 0.0,
    }


@dataclass
class _OdTotals:
    """Аккумуляторы поездок и загрузок за один проход по OD.

    Счётчики по маршрутам, направлениям, остановкам, парам (маршрут,
    направление) и сегментам; ``as_dict()`` отдаёт их в формате, который
    ожидают вызывающие.
    """

    assigned_trips: float = 0.0
    car_trips: float = 0.0
    walk_trips: float = 0.0
    two_wheel_trips: float = 0.0
    rest_trips: float = 0.0
    fare_revenue: float = 0.0
    period_total: float = 0.0
    route_totals: dict[int, float] = field(
        default_factory=lambda: defaultdict(float)
    )
    dir_totals: dict[tuple[int, int], float] = field(
        default_factory=lambda: defaultdict(float)
    )
    stop_totals: dict[str, dict[str, Any]] = field(
        default_factory=lambda: defaultdict(_stop_totals_entry)
    )
    route_stop_totals: dict[tuple[int, int], dict[str, float]] = field(
        default_factory=lambda: defaultdict(dict)
    )
    seg_totals: dict[tuple[int, int], float] = field(
        default_factory=lambda: defaultdict(float)
    )
    seg_forward_totals: dict[tuple[int, int], float] = field(
        default_factory=lambda: defaultdict(float)
    )
    seg_reverse_totals: dict[tuple[int, int], float] = field(
        default_factory=lambda: defaultdict(float)
    )
    seq_stop_totals: dict[tuple[int, int], float] = field(
        default_factory=lambda: defaultdict(float)
    )
    transfer_trips: float = 0.0
    interchanges: dict[tuple[int, int, int, int], dict[str, Any]] = field(default_factory=dict)
    total_commuters: float = 0.0
    covered_commuters: float = 0.0
    total_by_origin: np.ndarray | None = None
    covered_by_origin: np.ndarray | None = None
    no_route_by_origin: np.ndarray | None = None
    journey_origin_trips: np.ndarray | None = None
    journey_origin_journeys: np.ndarray | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "assigned_trips": self.assigned_trips,
            "car_trips": self.car_trips,
            "walk_trips": self.walk_trips,
            "two_wheel_trips": self.two_wheel_trips,
            "rest_trips": self.rest_trips,
            "fare_revenue": self.fare_revenue,
            "period_total": self.period_total,
            "route_totals": self.route_totals,
            "dir_totals": self.dir_totals,
            "stop_totals": self.stop_totals,
            "route_stop_totals": self.route_stop_totals,
            "seg_totals": self.seg_totals,
            "seg_forward_totals": self.seg_forward_totals,
            "seg_reverse_totals": self.seg_reverse_totals,
            "seq_stop_totals": self.seq_stop_totals,
            "transfer_trips": self.transfer_trips,
            "interchanges": self.interchanges,
            "total_commuters": self.total_commuters,
            "covered_commuters": self.covered_commuters,
            "total_by_origin": self.total_by_origin,
            "covered_by_origin": self.covered_by_origin,
            "no_route_by_origin": self.no_route_by_origin,
            "journey_origin_trips": self.journey_origin_trips,
            "journey_origin_journeys": self.journey_origin_journeys,
        }


# ===== Расстояния и режимы =====
def _base_time_for_pair(
    base_time_s: np.ndarray | None,
    period_index: int,
    pair_index: int,
    zi: int,
    zj: int,
    n_periods: int | None = None,
) -> float | None:
    if base_time_s is None:
        return None
    if base_time_s.ndim == 3:
        value = float(base_time_s[period_index, zi, zj])
    elif (
        base_time_s.ndim == 2
        and n_periods is not None
        and base_time_s.shape[0] >= n_periods
        and base_time_s.shape[0] != base_time_s.shape[1]
    ):
        value = float(base_time_s[period_index, pair_index])
    else:
        value = float(base_time_s[zi, zj])
    return value if value > 0.0 else None

def _segment_time_s(seq: dict[str, Any], seg_idx: int) -> float:
    """Время движения физического сегмента по cumT, без ожидания."""
    cum = seq.get("cum_t_s") or []
    if seg_idx < 0 or seg_idx >= len(cum):
        return 0.0
    if seg_idx + 1 < len(cum):
        return abs(float(cum[seg_idx + 1]) - float(cum[seg_idx]))
    if seq.get("closed") and cum:
        return max(0.0, float(seq.get("cycle_run_s", 0.0)) - float(cum[-1]))
    return 0.0




def _od_distance_meters(zones: Zones, zi: int, zj: int) -> float:
    """Хаверсиново расстояние между центрами зон (в метрах)."""
    return haversine_meters(
        float(zones.xy[zi][1]),
        float(zones.xy[zi][0]),
        float(zones.xy[zj][1]),
        float(zones.xy[zj][0]),
    )


def _apply_car_only_modes(
    totals: _OdTotals,
    *,
    trips: float,
    mode: ModeChoiceConfig | None,
    zones: Zones,
    zi: int,
    zj: int,
    od_meters: float | None = None,
    no_car_share: float | None = None,
    rest_s: float | None = None,
    car_period_multiplier: float = 1.0,
    car_base_time_s: float | None = None,
) -> None:
    """Fallback без транзитного пути: авто, пешком и (возможно) eBike.

    При ``mode is None`` не делает ничего (такие поездки не моделируются);
    при заданном mode — начисляет доли в соответствующие счётчики.
    """
    if mode is None:
        return
    if od_meters is None:
        od_meters = _od_distance_meters(zones, zi, zj)
    _transit, car_s, walk_s, ebike_s, rest_s = _takt_mode_shares(
        mode,
        od_meters,
        None,
        0.0,
        rest_s=rest_s,
        no_car_share=no_car_share,
        road_time_s=car_base_time_s,
        car_period_multiplier=car_period_multiplier,
    )
    totals.car_trips += trips * car_s
    totals.walk_trips += trips * walk_s
    totals.two_wheel_trips += trips * ebike_s
    totals.rest_trips += trips * rest_s


def _split_transit_trips(
    totals: _OdTotals,
    *,
    mode: ModeChoiceConfig,
    trips: float,
    od_meters: float,
    transit_s: float,
    base_time_s: float | None,
    no_car_share: float | None = None,
    car_period_multiplier: float = 1.0,
    car_base_time_s: float | None = None,
) -> float:
    """Считает mode shares по Takt и возвращает число транзитных поездок.

    Побочно начисляет авто/пешие/eBike-поездки и доход от тарифа.
    """
    fare_eur = _od_fare_eur(
        mode,
        od_meters,
        transit_s / 60.0 if transit_s > 0.0 else None,
    )
    transit_share, car_s, walk_s, ebike_s, rest_s = _takt_mode_shares(
        mode,
        od_meters,
        transit_s,
        fare_eur,
        rest_s=base_time_s,
        no_car_share=no_car_share,
        road_time_s=car_base_time_s,
        car_period_multiplier=car_period_multiplier,
    )
    totals.car_trips += trips * car_s
    totals.walk_trips += trips * walk_s
    totals.two_wheel_trips += trips * ebike_s
    totals.rest_trips += trips * rest_s
    transit_trips = trips * transit_share
    totals.fare_revenue += transit_trips * fare_eur
    return transit_trips


def _car_base_time_for_pair(
    car_base_time_s: np.ndarray | None,
    pair_index: int,
    zi: int,
    zj: int,
) -> float | None:
    """Resolve Takt OD fourth-field car base time."""
    if car_base_time_s is None:
        return None
    if car_base_time_s.ndim == 1:
        value = float(car_base_time_s[pair_index])
    else:
        value = float(car_base_time_s[zi, zj])
    return value if np.isfinite(value) and value >= 0.0 else None


def _journey_crowd_extra(
    journeys: list[_Journey],
    route_sequences: list[dict[str, Any]],
    crowd_state: Mapping[str, Mapping[tuple[int, int], float]] | None,
    seq_headway_min: Mapping[int, float] | None,
    wait_extra: Mapping[int, float] | None = None,
    period_index: int = 0,
    seq_jitter_s: Mapping[int, float] | None = None,
    include_first_leg_wait: bool = True,
    include_wait_extra: bool = True,
) -> np.ndarray:
    """Additional Takt crowd/reliability feedback in minutes."""
    seg_forward = crowd_state.get("seg_forward", {}) if crowd_state else {}
    seg_reverse = crowd_state.get("seg_reverse", {}) if crowd_state else {}
    unreliability = crowd_state.get("unreliability", {}) if crowd_state else {}
    result = np.zeros(len(journeys), dtype=np.float64)
    wait_extra = wait_extra or {}
    for jidx, journey in enumerate(journeys):
        extra_s = 0.0
        for leg_no, (seq_idx, a, b) in enumerate(journey.legs):
            selected = _route_segment_indices(route_sequences[seq_idx], a, b)
            if not selected or seq_headway_min is None or seq_idx not in seq_headway_min:
                continue
            crowd_seg, forward = selected[-1 if leg_no > 0 else 0]
            loads = seg_forward if forward else seg_reverse
            lf = max(1.0, float(loads.get((seq_idx, crowd_seg), 0.0)))
            if leg_no == 0:
                if include_first_leg_wait:
                    wait_s = _takt_po_seconds(float(seq_headway_min[seq_idx]))
                    unev = max(1.0, float(unreliability.get((seq_idx, period_index), 1.0)))
                    extra_s += wait_s * (unev * lf - 1.0)
            else:
                prev_seq, _prev_a, prev_b = journey.legs[leg_no - 1]
                prev_stop = route_sequences[prev_seq]["stops"][prev_b]
                curr_stop = route_sequences[seq_idx]["stops"][b]
                transfer_wait_min = _scheduled_transfer_wait_min(
                    prev_seq,
                    seq_idx,
                    prev_stop,
                    curr_stop,
                    stop_time_min=0.0,
                    route_stop_sequences=route_sequences,
                    seq_headway_min=seq_headway_min,
                    seq_jitter_s=seq_jitter_s or {},
                )
                extra_s += float(transfer_wait_min) * 60.0 * (lf - 1.0)
        if include_wait_extra:
            for seq_idx, _a, _b in journey.legs:
                extra_s += float(wait_extra.get(seq_idx, 0.0)) * 60.0
        result[jidx] = max(0.0, extra_s / 60.0)
    return result

def _accumulate_stop(
    entry: dict[str, Any],
    stop: dict[str, Any],
    *,
    is_boarding: bool,
    is_alighting: bool,
    trips: float,
) -> None:
    """Обновляет запись остановки: счётчики и стабильные метаданные."""
    if is_boarding:
        entry["boardings"] += trips
    if is_alighting:
        entry["alightings"] += trips
    entry["name"] = stop["name"]
    entry["lat"] = stop["lat"]
    entry["lon"] = stop["lon"]
    entry["stop_id"] = stop["id"]


def _accumulate_journey(
    totals: _OdTotals,
    route_sequences: list[dict[str, Any]],
    legs: tuple[tuple[int, int, int], ...],
    *,
    route_trips: float,
) -> None:
    """Добавляет поездки одного варианта по его ножкам в агрегаты.

    Для каждой ножки ``(seq_idx, orig_pos, dest_pos)`` обновляет итоги
    маршрута/направления и пробегает по остановкам маршрута, начисляя
    посадки/высадки и загрузку сегментов.
    """
    for seq_idx, orig_pos, dest_pos in legs:
        seq = route_sequences[seq_idx]
        rid = seq["route_id"]
        di = seq["di"]
        totals.route_totals[rid] += route_trips
        totals.dir_totals[(rid, di)] += route_trips

        rs_key = (rid, di)
        selected_segments = _route_segment_indices(seq, orig_pos, dest_pos)
        path_positions = [orig_pos]
        for seg_i, is_forward in selected_segments:
            arrival = (seg_i + 1) % len(seq["stops"]) if is_forward else seg_i
            path_positions.append(arrival)
        for path_no, si in enumerate(path_positions):
            stop = seq["stops"][si]
            _accumulate_stop(
                totals.stop_totals[_stop_key(stop)],
                stop,
                is_boarding=(path_no == 0),
                is_alighting=(path_no == len(path_positions) - 1),
                trips=route_trips,
            )
            if path_no == 0 or path_no == len(path_positions) - 1:
                totals.seq_stop_totals[(seq_idx, si)] += route_trips
            stop_name_key = stop["name"]
            totals.route_stop_totals[rs_key][stop_name_key] = (
                totals.route_stop_totals[rs_key].get(stop_name_key, 0.0)
                + route_trips
            )

        for seg_i, is_forward in selected_segments:
            totals.seg_totals[(seq_idx, seg_i)] += route_trips
            if is_forward:
                totals.seg_forward_totals[(seq_idx, seg_i)] += route_trips
            else:
                totals.seg_reverse_totals[(seq_idx, seg_i)] += route_trips


def _takt_first_leg_r_r_seconds(
    journey: _Journey,
    route_sequences: list[dict[str, Any]],
    *,
    period_index: int,
    seq_headway_min: Mapping[int, float] | None,
    crowd_state: Mapping[str, Mapping[tuple[int, int], float]] | None,
) -> float:
    """Return Takt first-leg Rr/co seconds for one journey."""
    seq_idx, a, b = journey.legs[0]
    headway = (
        float(seq_headway_min.get(seq_idx, 0.0))
        if seq_headway_min is not None
        else 0.0
    )
    wait_s = _takt_po_seconds(headway) if headway > 0.0 else 360.0
    seg_forward = crowd_state.get("seg_forward", {}) if crowd_state else {}
    seg_reverse = crowd_state.get("seg_reverse", {}) if crowd_state else {}
    unreliability = crowd_state.get("unreliability", {}) if crowd_state else {}
    selected = _route_segment_indices(route_sequences[seq_idx], a, b)
    load = 1.0
    if selected:
        seg_idx, forward = selected[0]
        loads = seg_forward if forward else seg_reverse
        load = max(1.0, float(loads.get((seq_idx, seg_idx), 0.0)))
    unev = max(1.0, float(unreliability.get((seq_idx, period_index), 1.0)))
    return wait_s * unev * load


def _takt_co_route_probs(
    journeys: list[_Journey],
    route_sequences: list[dict[str, Any]],
    *,
    period_index: int,
    seq_headway_min: Mapping[int, float] | None,
    crowd_state: Mapping[str, Mapping[tuple[int, int], float]] | None,
) -> np.ndarray:
    """Route split Takt co()/Rr() по первому boarding leg."""
    if not journeys:
        return np.zeros(0, dtype=np.float64)
    seg_forward = crowd_state.get("seg_forward", {}) if crowd_state else {}
    seg_reverse = crowd_state.get("seg_reverse", {}) if crowd_state else {}
    unreliability = crowd_state.get("unreliability", {}) if crowd_state else {}
    weights = np.zeros(len(journeys), dtype=np.float64)
    for idx, journey in enumerate(journeys):
        seq_idx, a, b = journey.legs[0]
        headway = float(seq_headway_min.get(seq_idx, 0.0)) if seq_headway_min is not None else 0.0
        wait_s = _takt_po_seconds(headway) if headway > 0.0 else 360.0
        selected = _route_segment_indices(route_sequences[seq_idx], a, b)
        lf = 1.0
        if selected:
            seg_idx, forward = selected[0]
            loads = seg_forward if forward else seg_reverse
            lf = max(1.0, float(loads.get((seq_idx, seg_idx), 0.0)))
        unev = max(1.0, float(unreliability.get((seq_idx, period_index), 1.0)))
        rr = wait_s * unev * lf
        weights[idx] = 1.0 / max(1.0, rr)
    total = float(weights.sum())
    if total <= 0.0:
        weights[0] = 1.0
        total = 1.0
    return weights / total


def _takt_leg_choice_probs(
    journey: _Journey,
    leg_index: int,
    route_sequences: list[dict[str, Any]],
    *,
    period_index: int,
    seq_headway_min: Mapping[int, float] | None,
    seq_jitter_s: Mapping[int, float] | None,
    crowd_state: Mapping[str, Mapping[tuple[int, int], float]] | None,
    transfer_index: Mapping[tuple[int, int], tuple[tuple[int, dict[str, Any], dict[str, Any]], ...]],
    stop_time_min: float,
    transfer_radius_m: float,
    ride_edge_cache: dict[tuple[int, int, int], float] | None = None,
) -> tuple[tuple[tuple[int, int, int], ...], np.ndarray]:
    """JS co(): normalized inverse-Rr weights for one journey leg."""
    candidates: list[tuple[int, int, int]] = [journey.legs[leg_index]]
    if leg_index > 0:
        candidates.extend(_leg_alternatives(
            journey, leg_index, route_sequences, transfer_index,
            stop_time_min=stop_time_min, crowd_state=crowd_state,
            transfer_radius_m=transfer_radius_m,
            ride_edge_cache=ride_edge_cache,
        ))
    seg_forward = crowd_state.get("seg_forward", {}) if crowd_state else {}
    seg_reverse = crowd_state.get("seg_reverse", {}) if crowd_state else {}
    unreliability = crowd_state.get("unreliability", {}) if crowd_state else {}
    prev = journey.legs[leg_index - 1] if leg_index > 0 else None
    weights = np.zeros(len(candidates), dtype=np.float64)
    for idx, (seq_idx, a, b) in enumerate(candidates):
        selected = _route_segment_indices(route_sequences[seq_idx], a, b)
        load = 1.0
        if selected:
            seg_i, forward = selected[0]
            loads = seg_forward if forward else seg_reverse
            load = max(1.0, float(loads.get((seq_idx, seg_i), 0.0)))
        if seq_headway_min is None or seq_idx not in seq_headway_min:
            rr_min = 1.0
        elif prev is None:
            wait_s = _takt_po_seconds(float(seq_headway_min[seq_idx]))
            unev = max(1.0, float(unreliability.get((seq_idx, period_index), 1.0)))
            rr_min = wait_s / 60.0 * unev * load
        else:
            prev_seq, _prev_a, prev_b = prev
            rr_min = _scheduled_transfer_wait_min(
                prev_seq, seq_idx,
                route_sequences[prev_seq]["stops"][prev_b],
                route_sequences[seq_idx]["stops"][a],
                stop_time_min=stop_time_min,
                route_stop_sequences=route_sequences,
                seq_headway_min=seq_headway_min,
                seq_jitter_s=seq_jitter_s or {},
            ) * load
        weights[idx] = 1.0 / max(1.0, rr_min)
    total = float(weights.sum())
    if total <= 0.0:
        weights[0] = 1.0
        total = 1.0
    return tuple(candidates), weights / total

def _accumulate_transit_journeys(
    totals: _OdTotals,
    journeys: list[_Journey],
    travel_times: np.ndarray,
    *,
    transit_trips: float,
    route_sequences: list[dict[str, Any]],
    period_index: int = 0,
    seq_headway_min: Mapping[int, float] | None = None,
    seq_jitter_s: Mapping[int, float] | None = None,
    crowd_state: Mapping[str, Mapping[tuple[int, int], float]] | None = None,
    transfer_index: Mapping[tuple[int, int], tuple[tuple[int, dict[str, Any], dict[str, Any]], ...]] | None = None,
    stop_time_min: float = 0.0,
    transfer_radius_m: float = 800.0,
    ride_edge_cache: dict[tuple[int, int, int], float] | None = None,
) -> None:
    """Journey share plus JS-style co() branching independently per leg."""
    journey_probs = _takt_co_route_probs(
        journeys, route_sequences,
        period_index=period_index,
        seq_headway_min=seq_headway_min,
        crowd_state=crowd_state,
    )
    totals.assigned_trips += transit_trips
    transfer_index = transfer_index or {}
    for ji, journey in enumerate(journeys):
        journey_trips = transit_trips * journey_probs[ji]
        if journey_trips <= 0.0:
            continue
        if len(journey.legs) > 1:
            totals.transfer_trips += journey_trips * (len(journey.legs) - 1)
            for left, right in zip(journey.legs, journey.legs[1:]):
                key = (int(left[0]), int(left[2]), int(right[0]), int(right[1]))
                item = totals.interchanges.get(key)
                if item is None:
                    item = {"trips": 0.0, "periods": [0.0] * 5}
                    totals.interchanges[key] = item
                item["trips"] += journey_trips
                if 0 <= period_index < 5:
                    item["periods"][period_index] += journey_trips
        for leg_index in range(len(journey.legs)):
            candidates, leg_probs = _takt_leg_choice_probs(
                journey, leg_index, route_sequences,
                period_index=period_index,
                seq_headway_min=seq_headway_min,
                seq_jitter_s=seq_jitter_s,
                crowd_state=crowd_state,
                transfer_index=transfer_index,
                stop_time_min=stop_time_min,
                transfer_radius_m=transfer_radius_m,
                ride_edge_cache=ride_edge_cache,
            )
            for ci, leg in enumerate(candidates):
                leg_trips = journey_trips * float(leg_probs[ci])
                if leg_trips > 0.0:
                    _accumulate_journey(
                        totals, route_sequences, (leg,), route_trips=leg_trips
                    )
# ===== Главная точка входа =====


def _assign_od(
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
    logit_temp: float,
    out_factor: float,
    ret_factor: float,
    wait_extra: Mapping[int, float] | None = None,
    mode: ModeChoiceConfig | None,
    zones: Zones,
    transfer_penalty_calc: str = "takt",
    seq_headway_min: Mapping[int, float] | None = None,
    seq_jitter_s: Mapping[int, float] | None = None,
    no_car_shares: np.ndarray | None = None,
    wait_calc: str = "takt",
    base_time_s: np.ndarray | None = None,
    period_index: int = 0,
    crowd_state: Mapping[str, Mapping[tuple[int, int], float]] | None = None,
    transfer_index: Mapping[tuple[int, int], tuple[tuple[int, dict[str, Any], dict[str, Any]], ...]] | None = None,
    car_period_multiplier: float = 1.0,
    car_base_time_s: np.ndarray | None = None,
    od_distances_m: np.ndarray | None = None,
    ride_edge_cache: dict[tuple[int, int, int], float] | None = None,
    access_cache: dict[int, list[tuple[int, int, int, float]]] | None = None,
    perf_stats: dict[str, float] | None = None,
    journey_cache: dict[tuple[Any, ...], list[_Journey]] | None = None,
) -> dict[str, Any]:
    """Один проход распределения по всем OD-парам; возвращает агрегаты."""
    assign_started = perf_counter() if perf_stats is not None else 0.0
    if perf_stats is not None:
        perf_stats.setdefault("assignment_s", 0.0)
        perf_stats.setdefault("access_s", 0.0)
        perf_stats.setdefault("journeys_s", 0.0)
        perf_stats.setdefault("access_cache_misses", 0.0)

    totals = _OdTotals()
    n_zones = len(zones)
    totals.total_by_origin = np.zeros(n_zones, dtype=np.float64)
    totals.covered_by_origin = np.zeros(n_zones, dtype=np.float64)
    totals.no_route_by_origin = np.zeros(n_zones, dtype=np.float64)
    totals.journey_origin_trips = np.zeros(n_zones, dtype=np.float64)
    totals.journey_origin_journeys = np.zeros(n_zones, dtype=np.float64)
    if ride_edge_cache is None:
        ride_edge_cache = {}
    if access_cache is None:
        access_cache = {}
    if journey_cache is None:
        journey_cache = {}

    for idx in range(len(od_rows)):
        zi = int(od_rows[idx])
        zj = int(od_cols[idx])
        raw_trips = float(od_vals[idx])
        if raw_trips <= 0:
            continue
        if period_index == 0:
            totals.total_commuters += raw_trips
            totals.total_by_origin[zi] += raw_trips
        trips = raw_trips
        # Внутризонные поездки не моделируются общественным транспортом
        if zi == zj:
            continue

        # Takt evaluates each OD pair with the period's out+ret demand share.
        factor = float(out_factor) + float(ret_factor)
        trips *= factor
        if trips <= 0:
            continue
        totals.period_total += trips
        road_time_s = _base_time_for_pair(base_time_s, period_index, idx, zi, zj, n_periods=5)
        car_base_time_pair_s = _car_base_time_for_pair(car_base_time_s, idx, zi, zj)
        od_meters = (float(od_distances_m[idx]) if od_distances_m is not None else _od_distance_meters(zones, zi, zj))

        access_started = perf_counter() if perf_stats is not None else 0.0
        origin_stops = access_cache.get(zi)
        if origin_stops is None:
            origin_stops = _line_access_stops(
                zone_nearest.get(zi, []), route_sequences
            )
            access_cache[zi] = origin_stops
            if perf_stats is not None:
                perf_stats["access_cache_misses"] += 1.0
        destination_stops = access_cache.get(zj)
        if destination_stops is None:
            destination_stops = _line_access_stops(
                zone_nearest.get(zj, []), route_sequences
            )
            access_cache[zj] = destination_stops
            if perf_stats is not None:
                perf_stats["access_cache_misses"] += 1.0
        if perf_stats is not None:
            perf_stats["access_s"] += perf_counter() - access_started
        if period_index == 0 and (origin_stops or destination_stops):
            totals.covered_commuters += raw_trips
        if not origin_stops or not destination_stops:
            if period_index == 0:
                totals.no_route_by_origin[zi] += raw_trips
            _apply_car_only_modes(
                totals,
                trips=trips,
                mode=mode,
                zones=zones,
                zi=zi,
                zj=zj,
                od_meters=od_meters,
                no_car_share=(
                    float(no_car_shares[zi]) if no_car_shares is not None else None
                ),
                rest_s=road_time_s,
                car_period_multiplier=car_period_multiplier,
                car_base_time_s=car_base_time_pair_s,
            )
            continue

        journey_key = (
            zi,
            zj,
            id(crowd_state),
            id(seq_headway_min),
            id(seq_jitter_s),
            road_time_s,
            od_meters,
        )
        journeys = journey_cache.get(journey_key)
        if journeys is None:
            journeys = build_journeys(
                origin_stops,
                destination_stops,
            route_sequences,
            stop_time_min=stop_time_min,
            wait_time_min=wait_time_min,
            walk_to_stop_min=walk_to_stop_min,
            transfer_penalty_min=transfer_penalty_min,
            transfer_wait_min=transfer_wait_min,
            transfer_radius_m=transfer_radius_m,
            max_transfers=max_transfers,
            transfer_penalty_calc=transfer_penalty_calc,
            seq_headway_min=seq_headway_min,
            seq_jitter_s=seq_jitter_s,
            wait_calc=wait_calc,
            crowd_state=crowd_state,
            transfer_index=transfer_index,
            od_distance_m=od_meters,
            road_time_s=road_time_s,
            ride_edge_cache=ride_edge_cache,
                perf_stats=perf_stats,
            )
            journey_cache[journey_key] = journeys
        elif perf_stats is not None:
            perf_stats["journey_cache_hits"] = perf_stats.get("journey_cache_hits", 0.0) + 1.0
        if not journeys:
            if period_index == 0:
                totals.no_route_by_origin[zi] += raw_trips
            _apply_car_only_modes(
                totals,
                trips=trips,
                mode=mode,
                zones=zones,
                zi=zi,
                zj=zj,
                no_car_share=(
                    float(no_car_shares[zi]) if no_car_shares is not None else None
                ),
                rest_s=_base_time_for_pair(base_time_s, period_index, idx, zi, zj, n_periods=5),
            )
            continue

        # Время каждой поездки; перегрузка ожидания (crowding) добавляется
        # по маршрутам его ножек.
        raw_times = np.asarray([j.total_time_min for j in journeys], dtype=np.float64)
        travel_times = raw_times + _journey_crowd_extra(
            journeys,
            route_sequences,
            crowd_state,
            seq_headway_min,
            wait_extra=wait_extra,
            period_index=period_index,
            seq_jitter_s=seq_jitter_s,
            include_first_leg_wait=False,
            include_wait_extra=False,
        )

        if mode is not None:
            transit_cost_s = float(
                _takt_route_choice(
                    travel_times * 60.0,
                    np.asarray(
                        [
                            _takt_first_leg_r_r_seconds(
                                journey,
                                route_sequences,
                                period_index=period_index,
                                seq_headway_min=seq_headway_min,
                                crowd_state=crowd_state,
                            )
                            for journey in journeys
                        ],
                        dtype=np.float64,
                    ),
                )[1]
            )
            transit_trips = _split_transit_trips(
                totals,
                mode=mode,
                trips=trips,
                od_meters=od_meters,
                transit_s=transit_cost_s,
                base_time_s=road_time_s,
                no_car_share=(
                    float(no_car_shares[zi]) if no_car_shares is not None else None
                ),
                car_period_multiplier=car_period_multiplier,
                car_base_time_s=car_base_time_pair_s,
            )
        else:
            transit_trips = trips

        if transit_trips <= 0:
            continue

        if period_index == 0:
            totals.covered_by_origin[zi] += raw_trips
        totals.journey_origin_trips[zi] += raw_trips * float(out_factor)
        totals.journey_origin_journeys[zi] += transit_trips * float(out_factor)

        _accumulate_transit_journeys(
            totals,
            journeys,
            travel_times,
            transit_trips=transit_trips,
            route_sequences=route_sequences,
            period_index=period_index,
            seq_headway_min=seq_headway_min,
            crowd_state=crowd_state,
            seq_jitter_s=seq_jitter_s,
            transfer_index=transfer_index,
            stop_time_min=stop_time_min,
            transfer_radius_m=transfer_radius_m,
            ride_edge_cache=ride_edge_cache,
        )

    return totals.as_dict()