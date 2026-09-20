"""Per-OD assignment of trips to routes and mode shares.

Extracted from `passenger_flow/core.py`: one pass over all OD pairs, journey
enumeration, logit split and accumulation of route/direction/stop totals.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ...od import Zones
from ..base.models import ModeChoiceConfig
from ..base.takt import (
    _takt_crowding_ride_mult,
    _takt_crowding_wait_mult,
    _takt_po_seconds,
)
from ..network.geometry import _stop_key, haversine_meters
from ..network.routes import _route_segment_indices, build_journeys
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
) -> list[tuple[int, int, int]]:
    """Оставляет кандидаты-остановки, попадающие в радиус доступа линии.

    Каждый кандидат зоны — ``(seq_idx, stop_idx, position, dist_m)``; линия
    обслуживает точку, только если расстояние до остановки <= её ``access_m``
    (движок Takt: ``nearD[c] <= access``).
    """
    kept: list[tuple[int, int, int]] = []
    for seqi, stopi, posi, dist_m in candidates:
        if dist_m <= float(route_sequences[seqi]["access_m"]):
            kept.append((seqi, stopi, posi))
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
        }


# ===== Расстояния и режимы =====
def _base_time_for_pair(
    base_time_s: np.ndarray | None,
    period_index: int,
    zi: int,
    zj: int,
) -> float | None:
    if base_time_s is None:
        return None
    if base_time_s.ndim == 3:
        value = float(base_time_s[period_index, zi, zj])
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
    no_car_share: float | None = None,
    rest_s: float | None = None,
    car_period_multiplier: float = 1.0,
) -> None:
    """Fallback без транзитного пути: авто, пешком и (возможно) eBike.

    При ``mode is None`` не делает ничего (такие поездки не моделируются);
    при заданном mode — начисляет доли в соответствующие счётчики.
    """
    if mode is None:
        return
    od_meters = _od_distance_meters(zones, zi, zj)
    _transit, car_s, walk_s, ebike_s, rest_s = _takt_mode_shares(
        mode,
        od_meters,
        None,
        0.0,
        rest_s=rest_s,
        no_car_share=no_car_share,
        road_time_s=rest_s,
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
        road_time_s=base_time_s,
        car_period_multiplier=car_period_multiplier,
    )
    totals.car_trips += trips * car_s
    totals.walk_trips += trips * walk_s
    totals.two_wheel_trips += trips * ebike_s
    totals.rest_trips += trips * rest_s
    transit_trips = trips * transit_share
    totals.fare_revenue += transit_trips * fare_eur
    return transit_trips


def _journey_crowd_extra(
    journeys: list[_Journey],
    route_sequences: list[dict[str, Any]],
    crowd_state: Mapping[str, Mapping[tuple[int, int], float]] | None,
    seq_headway_min: Mapping[int, float] | None,
    wait_extra: Mapping[int, float] | None = None,
) -> np.ndarray:
    """Дополнительное время поездки от сегментной и остановочной перегрузки."""
    seg_forward = crowd_state.get("seg_forward", {}) if crowd_state else {}
    seg_reverse = crowd_state.get("seg_reverse", {}) if crowd_state else {}
    stop_extra = crowd_state.get("stop_extra", {}) if crowd_state else {}
    result = np.zeros(len(journeys), dtype=np.float64)
    wait_extra = wait_extra or {}
    for jidx, journey in enumerate(journeys):
        extra_s = 0.0
        for seq_idx, a, b in journey[1]:
            seq = route_sequences[seq_idx]
            extra_s += float(wait_extra.get(seq_idx, 0.0)) * 60.0
            selected_segments = _route_segment_indices(seq, a, b)
            for seg_i, _is_forward in selected_segments:
                lf = max(
                    float(seg_forward.get((seq_idx, seg_i), 0.0)),
                    float(seg_reverse.get((seq_idx, seg_i), 0.0)),
                )
                if lf <= 0.0:
                    continue
                extra_s += _segment_time_s(seq, seg_i) * (
                    _takt_crowding_ride_mult(lf) - 1.0
                )
            if selected_segments:
                prev_stop = a
                for seg_i, is_forward in selected_segments:
                    if is_forward:
                        arrival_stop = (seg_i + 1) % len(seq["stops"])
                    else:
                        arrival_stop = seg_i % len(seq["stops"])
                    if arrival_stop != prev_stop:
                        extra_s += float(stop_extra.get((seq_idx, arrival_stop), 0.0))
                    prev_stop = arrival_stop
            if seq_headway_min is not None and seq_idx in seq_headway_min and selected_segments:
                first_seg, _first_forward = selected_segments[0]
                lf = max(
                    float(seg_forward.get((seq_idx, first_seg), 0.0)),
                    float(seg_reverse.get((seq_idx, first_seg), 0.0)),
                )
                if lf > 1.0:
                    base_wait_s = _takt_po_seconds(
                        float(seq_headway_min[seq_idx])
                    )
                    extra_s += base_wait_s * (
                        _takt_crowding_wait_mult(lf) - 1.0
                    )
        result[jidx] = extra_s / 60.0
    return result


# ===== Накопление загрузок по вариантам =====


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


def _accumulate_transit_journeys(
    totals: _OdTotals,
    journeys: list[_Journey],
    travel_times: np.ndarray,
    *,
    transit_trips: float,
    route_sequences: list[dict[str, Any]],
) -> None:
    """Частотный сплит Takt по вариантам и накопление их загрузок."""
    probs = _takt_route_probs(travel_times * 60.0)
    totals.assigned_trips += transit_trips

    for ci, journey in enumerate(journeys):
        route_trips = transit_trips * probs[ci]
        legs = journey.legs
        if route_trips <= 0:
            continue
        _accumulate_journey(
            totals, route_sequences, legs, route_trips=route_trips
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
    car_period_multiplier: float = 1.0,
) -> dict[str, Any]:
    """Один проход распределения по всем OD-парам; возвращает агрегаты."""
    totals = _OdTotals()

    for idx in range(len(od_rows)):
        zi = int(od_rows[idx])
        zj = int(od_cols[idx])
        trips = float(od_vals[idx])
        if trips <= 0:
            continue
        # Внутризонные поездки не моделируются общественным транспортом
        if zi == zj:
            continue

        factor = out_factor if zi < zj else ret_factor
        trips *= factor
        if trips <= 0:
            continue
        totals.period_total += trips
        road_time_s = _base_time_for_pair(base_time_s, period_index, zi, zj)

        origin_stops = _line_access_stops(
            zone_nearest.get(zi, []), route_sequences
        )
        destination_stops = _line_access_stops(
            zone_nearest.get(zj, []), route_sequences
        )
        if not origin_stops or not destination_stops:
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
                rest_s=road_time_s,
                car_period_multiplier=car_period_multiplier,
            )
            continue

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
        )
        if not journeys:
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
                rest_s=_base_time_for_pair(base_time_s, period_index, zi, zj),
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
        )

        if mode is not None:
            transit_trips = _split_transit_trips(
                totals,
                mode=mode,
                trips=trips,
                od_meters=_od_distance_meters(zones, zi, zj),
                transit_s=float(_takt_route_choice(travel_times * 60.0)[1]),
                base_time_s=road_time_s,
                no_car_share=(
                    float(no_car_shares[zi]) if no_car_shares is not None else None
                ),
                car_period_multiplier=car_period_multiplier,
            )
        else:
            transit_trips = trips

        if transit_trips <= 0:
            continue

        _accumulate_transit_journeys(
            totals,
            journeys,
            travel_times,
            transit_trips=transit_trips,
            route_sequences=route_sequences,
        )

    return totals.as_dict()