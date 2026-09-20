"""Operational line KPIs: cycle, fleet, cost and crowding.

Extracted from `passenger_flow/core.py`: per-route operating metrics given a
service headway.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from ..base.models import (
    LineResult,
    VehicleSpec,
    vehicle_spec_for_route_type,
)
from ..base.takt import _takt_crowding_km_classes
from ..network.geometry import haversine_meters


def _route_cycle(
    seq: dict[str, Any], spec: VehicleSpec
) -> tuple[float, float]:
    """Оборот маршрута: (длина туда+обратно, км; время оборота, мин).

    Время: движение со скоростью спецификации + ``dwell_s`` на остановку
    + ``turnback_s`` на каждой конечной.
    """
    stops = seq["stops"]
    meters = 0.0
    for i in range(len(stops) - 1):
        meters += haversine_meters(
            stops[i]["lat"], stops[i]["lon"], stops[i + 1]["lat"], stops[i + 1]["lon"]
        )
    run_s = meters / max(spec.speed_kmh / 3.6, 0.01)
    dwell = len(stops) * spec.dwell_s
    cycle_min = 2.0 * (run_s + dwell + 2.0 * spec.turnback_s) / 60.0
    cycle_km = 2.0 * meters / 1000.0
    return cycle_km, cycle_min

def _build_line_kpis(
    route_sequences: list[dict[str, Any]],
    route_totals: Mapping[int, float],
    headway_min: float,
    revenue_day: float,
    assigned_trips: float,
    vehicle_specs: Mapping[str, VehicleSpec] | None,
    headway_by_route: Mapping[int, float] | None = None,
    capex_factor: float = 1.0,
    capex_amort_years: float = 30.0,
    seg_totals: Mapping[tuple[int, int], float] | None = None,
) -> list[LineResult]:
    """Эксплуатационные KPI линий (при заданном ``headway_min``).

    ``headway_by_route`` переопределяет интервал по конкретному маршруту;
    ``capex_amort_years`` — срок амортизации капитальных вложений
    (``capex_day = length_km·capex_eur_per_km·capex_factor / (365·срок)``).

    Пассажиро-километры считаются по сегментам между остановками
    (``seg_totals[(seq_idx, seg_i)]`` — поездки по сегменту направления),
    если ``seg_totals`` задан; иначе — по средней длине направления.
    """
    results: list[LineResult] = []
    seen: set[int] = set()
    for seq in route_sequences:
        rid = seq["route_id"]
        if rid in seen:
            continue
        seen.add(rid)
        trips = float(route_totals.get(rid, 0.0))
        key = str(seq.get("route_type_key") or "").lower()
        spec = vehicle_specs.get(key) if vehicle_specs else None
        if spec is None:
            spec = vehicle_spec_for_route_type(key)
        hv = (
            headway_by_route.get(rid, headway_min)
            if headway_by_route is not None
            else headway_min
        )
        runs_day = 1440.0 / max(float(hv), 1e-6)
        cycle_km, cycle_min = _route_cycle(seq, spec)
        fleet = math.ceil(cycle_min / max(float(hv), 1e-6))
        share = trips / assigned_trips if assigned_trips > 0.0 else 0.0
        capacity = spec.capacity

        # Пассажиро-километры и классы: по сегментам направления при наличии
        # seg_totals (Takt: segP → passengerKm/классы по nt сегмента).
        pkm = crowded_km = excess_km = severe_km = extreme_km = 0.0
        oneway_km = 0.5 * cycle_km
        max_nt = 0.0
        if seg_totals is not None:
            stops = seq["stops"]
            seg_km = [
                haversine_meters(
                    stops[i]["lat"], stops[i]["lon"],
                    stops[i + 1]["lat"], stops[i + 1]["lon"],
                )
                / 1000.0
                for i in range(len(stops) - 1)
            ]
            for seg_i, km in enumerate(seg_km):
                seg_pax = float(seg_totals.get((seq.get("_seq_idx", -1), seg_i), 0.0))
                if seg_pax <= 0.0:
                    continue
                nt = (
                    seg_pax / (capacity * runs_day)
                    if capacity > 0 and runs_day > 0
                    else 0.0
                )
                max_nt = max(max_nt, nt)
                seg_pkm, seg_c, seg_e, seg_s, seg_x = _takt_crowding_km_classes(
                    nt, seg_pax, km, capacity, runs_day
                )
                pkm += seg_pkm
                crowded_km += seg_c
                excess_km += seg_e
                severe_km += seg_s
                extreme_km += seg_x
        else:
            crowding = (
                trips / (capacity * runs_day)
                if capacity > 0 and runs_day > 0
                else 0.0
            )
            max_nt = crowding
            if oneway_km > 0.0 and crowding > 0.0:
                pkm, crowded_km, excess_km, severe_km, extreme_km = (
                    _takt_crowding_km_classes(
                        crowding, trips, oneway_km, capacity, runs_day
                    )
                )
        results.append(
            LineResult(
                route_id=rid,
                route_name=seq["route_name"],
                mode=seq["route_type"],
                trips=trips,
                cycle_km=cycle_km,
                cycle_min=cycle_min,
                fleet=fleet,
                veh_km_day=cycle_km * runs_day,
                opex_day=cycle_km * runs_day * spec.opex_per_veh_km,
                revenue_day=revenue_day * share,
                capex_day=(
                    cycle_km
                    / 2.0
                    * spec.capex_eur_per_km
                    * capex_factor
                    / max(365.0 * max(float(capex_amort_years), 0.01), 1.0)
                ),
                crowding=max_nt,
                min_headway=60.0 / max(spec.track_tph, 1e-6),
                passenger_km=pkm,
                crowded_passenger_km=crowded_km,
                excess_passenger_km=excess_km,
                severe_passenger_km=severe_km,
                extreme_passenger_km=extreme_km,
            )
        )
    results.sort(key=lambda r: -r.trips)
    return results
