"""Operational line KPIs: cycle, fleet, cost and crowding.

Extracted from `passenger_flow/core.py`: per-route operating metrics given a
service headway.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, Sequence

from ..base.models import (
    LineResult,
    VehicleSpec,
    vehicle_spec_for_route_type,
)
from ..base.takt import TAKT_FLEET, _takt_crowding_km_classes
from ..network.geometry import haversine_meters


def _infra_stop_key(stop: Mapping[str, Any]) -> str:
    """Канонический ключ физической остановки для shared-track секций."""
    if stop.get("id") is not None:
        return f"id:{stop['id']}"
    return f"xy:{float(stop['lat']):.6f},{float(stop['lon']):.6f}"


def _atomic_infrastructure_sections(
    route_sequences: list[dict[str, Any]],
) -> tuple[
    dict[tuple[str, str, str], set[int]],
    dict[tuple[int, int], list[tuple[tuple[str, str, str], float]]],
]:
    """Разбивает линейные сегменты по вершинам других линий, как Takt Ja/Xa.

    Входные маршруты представлены остановочными полилиниями, поэтому
    используем локальную equirectangular-проекцию и делим сегмент в точках
    других маршрутов, лежащих на той же геометрии. Это позволяет обнаружить
    общий участок даже если одна линия имеет промежуточную остановку, а другая нет.
    """
    section_lines: dict[tuple[str, str, str], set[int]] = {}
    by_segment: dict[tuple[int, int], list[tuple[tuple[str, str, str], float]]] = {}
    segment_points: list[tuple[int, int, int, str, float, float]] = []
    for seq_idx, seq in enumerate(route_sequences):
        stops = seq.get("stops") or []
        mode = str(seq.get("route_type_key") or "").lower()
        n = len(stops)
        seg_count = n if seq.get("closed") else max(0, n - 1)
        for seg_i in range(seg_count):
            a = stops[seg_i]; b = stops[(seg_i + 1) % n]
            segment_points.append((
                seq_idx, seg_i, int(seq["route_id"]), mode,
                float(a["lat"]), float(a["lon"]),
            ))
    # Все вершины потенциального overlap-кандидата собираются по mode.
    vertices_by_mode: dict[str, list[tuple[float, float]]] = {}
    for seq in route_sequences:
        mode = str(seq.get("route_type_key") or "").lower()
        bucket = vertices_by_mode.setdefault(mode, [])
        bucket.extend((float(st["lat"]), float(st["lon"])) for st in (seq.get("stops") or []))

    def project(lat: float, lon: float, lat0: float) -> tuple[float, float]:
        rad = math.pi / 180.0
        x = (lon * rad) * math.cos(lat0 * rad) * 6_371_000.0
        y = (lat * rad) * 6_371_000.0
        return x, y

    def point_on_segment(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float | None:
        vx, vy = bx - ax, by - ay
        wx, wy = px - ax, py - ay
        length2 = vx * vx + vy * vy
        if length2 <= 1e-12:
            return None
        cross = abs(vx * wy - vy * wx) / math.sqrt(length2)
        if cross > 0.5:
            return None
        t = (wx * vx + wy * vy) / length2
        if t <= 1e-9 or t >= 1.0 - 1e-9:
            return None
        return t

    for seq_idx, seg_i, rid, mode, alat, alon in segment_points:
        seq = route_sequences[seq_idx]
        stops = seq["stops"]
        bstop = stops[(seg_i + 1) % len(stops)]
        blat, blon = float(bstop["lat"]), float(bstop["lon"])
        lat0 = (alat + blat) * 0.5
        ax, ay = project(alat, alon, lat0)
        bx, by = project(blat, blon, lat0)
        cuts = [(0.0, alat, alon), (1.0, blat, blon)]
        for plat, plon in vertices_by_mode.get(mode, []):
            px, py = project(plat, plon, lat0)
            t = point_on_segment(px, py, ax, ay, bx, by)
            if t is not None:
                cuts.append((t, plat, plon))
        cuts.sort(key=lambda item: item[0])
        unique: list[tuple[float, float, float]] = []
        for cut in cuts:
            if not unique or abs(cut[0] - unique[-1][0]) > 1e-8:
                unique.append(cut)
        pieces: list[tuple[tuple[str, str, str], float]] = []
        for left, right in zip(unique, unique[1:]):
            if right[0] - left[0] <= 1e-9:
                continue
            p1 = f"{left[1]:.5f},{left[2]:.5f}"
            p2 = f"{right[1]:.5f},{right[2]:.5f}"
            edge = tuple(sorted((p1, p2)))
            key = (mode, edge[0], edge[1])
            length_m = haversine_meters(left[1], left[2], right[1], right[2])
            pieces.append((key, length_m))
            section_lines.setdefault(key, set()).add(rid)
        by_segment[(seq_idx, seg_i)] = pieces
    return section_lines, by_segment

def _shared_capacity_min_headways(
    route_sequences: list[dict[str, Any]],
    default_headway_min: float,
    headway_by_route: Mapping[int, float] | None,
) -> dict[int, float]:
    """Минимальный интервал с учётом shared infrastructure, по модели Takt Ga.

    Секция определяется физической парой соседних открытых остановок и типом
    транспорта. Для каждой линии residual capacity равна `track_tph` секции
    минус частота остальных линий, использующих ту же секцию.
    """
    section_lines, _ = _atomic_infrastructure_sections(route_sequences)
    min_headway: dict[int, float] = {rid: 60.0 / max(tph, 1e-9) for rid, tph in line_limit.items()}
    line_headway = lambda rid: float(headway_by_route.get(rid, default_headway_min)) if headway_by_route is not None else float(default_headway_min)
    for key, lines in section_lines.items():
        if len(lines) < 2:
            continue
        mode = key[0]
        limit = min(
            float(vehicle_spec_for_route_type(mode).track_tph),
            *(line_limit.get(rid, 0.0) for rid in lines),
        )
        for rid in lines:
            own_tph = 60.0 / max(line_headway(rid), 1e-9)
            others_tph = sum(
                60.0 / max(line_headway(other), 1e-9)
                for other in lines if other != rid
            )
            residual = limit - others_tph
            min_headway[rid] = max(min_headway.get(rid, 0.0), 60.0 / residual) if residual > 0.01 else math.inf
    return min_headway

def _sequence_capital_cost_eur(
    seq: Mapping[str, Any],
    spec: VehicleSpec,
    capex_factor: float,
    shared_sections: set[tuple[str, str, str]] | None = None,
    atomic_sections: Mapping[tuple[int, int], list[tuple[tuple[str, str, str], float]]] | None = None,
) -> float:
    """Сегментный CAPEX Takt с поддержкой row/segCostMul/fixedLegs/gaps.
    При переданном ``shared_sections`` одинаковые физические секции
    оплачиваются один раз, как reuse-track часть La().

    При отсутствии явной инфраструктурной разметки возвращает прежний
    type-level fallback через ``spec.capex_eur_per_km``.
    """
    stops = seq.get("stops") or []
    n = len(stops)
    if n < 2:
        return 0.0
    closed = bool(seq.get("closed"))
    seg_count = n if closed else n - 1
    rows = seq.get("rows")
    fixed = seq.get("fixed_legs") or []
    gaps = seq.get("gaps") or []
    multipliers = seq.get("seg_cost_mul") or []
    explicit = bool(seq.get("row_explicit")) or bool(rows) or bool(fixed) or bool(gaps) or bool(multipliers)
    if not explicit:
        cycle_one_way_km = sum(
            haversine_meters(
                stops[i]["lat"], stops[i]["lon"],
                stops[(i + 1) % n]["lat"], stops[(i + 1) % n]["lon"],
            ) / 1000.0
            for i in range(seg_count)
        )
        multiplier = 2.0 if closed and seq.get("both_ways") else 1.0
        return cycle_one_way_km * float(spec.capex_eur_per_km) * float(capex_factor) * multiplier

    fleet = TAKT_FLEET.get(str(seq.get("route_type_key") or "").lower(), {})
    row_table = fleet.get("rows", {}) if isinstance(fleet, Mapping) else {}
    default_row = seq.get("row") or (fleet.get("default_row") if isinstance(fleet, Mapping) else None)
    total = 0.0
    for i in range(seg_count):
        if i < len(gaps) and bool(gaps[i]):
            continue
        fixed_leg = fixed[i] if i < len(fixed) else None
        if isinstance(fixed_leg, Mapping):
            if bool(fixed_leg.get("rebuild")) and fixed_leg.get("rebuildCostM") is not None:
                total += max(0.0, float(fixed_leg["rebuildCostM"])) * 1e6 * float(capex_factor)
                continue
            if fixed_leg.get("authored") is False and fixed_leg.get("costM") is not None:
                total += max(0.0, float(fixed_leg["costM"])) * 1e6 * float(capex_factor)
                continue
        row = default_row
        if isinstance(rows, (list, tuple)) and i < len(rows) and rows[i] is not None:
            row = rows[i]
        elif isinstance(rows, Mapping) and i in rows:
            row = rows[i]
        cost_per_km_m = None
        if isinstance(row_table, Mapping):
            data = row_table.get(row)
            if data is None:
                data = row_table.get(str(row))
            if isinstance(data, Mapping) and data.get("cost_per_km") is not None:
                cost_per_km_m = float(data["cost_per_km"])
        if cost_per_km_m is None:
            cost_per_km_eur = float(spec.capex_eur_per_km)
        else:
            cost_per_km_eur = cost_per_km_m * 1e6
        multiplier = 1.0
        if isinstance(multipliers, (list, tuple)) and i < len(multipliers):
            try:
                multiplier = max(0.0, float(multipliers[i]))
            except (TypeError, ValueError):
                multiplier = 1.0
        pieces = (
            atomic_sections.get((int(seq.get("_seq_idx", -1)), i), [])
            if atomic_sections is not None
            else []
        )
        if pieces:
            for section_key, length_m in pieces:
                if shared_sections is not None:
                    if section_key in shared_sections:
                        continue
                    shared_sections.add(section_key)
                total += (length_m / 1000.0) * cost_per_km_eur * multiplier * float(capex_factor)
        else:
            distance_km = haversine_meters(
                stops[i]["lat"], stops[i]["lon"],
                stops[(i + 1) % n]["lat"], stops[(i + 1) % n]["lon"],
            ) / 1000.0
            section_key = tuple(sorted((_infra_stop_key(stops[i]), _infra_stop_key(stops[(i + 1) % n]))))
            if shared_sections is None or section_key not in shared_sections:
                if shared_sections is not None:
                    shared_sections.add(section_key)
                total += distance_km * cost_per_km_eur * multiplier * float(capex_factor)
    return total

def _route_cycle(
    seq: dict[str, Any], spec: VehicleSpec
) -> tuple[float, float]:
    """Оборот Takt: (полный оборот км, полный оборот мин)."""
    stops = seq["stops"]
    closed = bool(seq.get("closed"))
    one_way_km = 0.0
    for i in range(len(stops) - 1):
        one_way_km += haversine_meters(
            stops[i]["lat"], stops[i]["lon"],
            stops[i + 1]["lat"], stops[i + 1]["lon"],
        ) / 1000.0
    if closed and len(stops) >= 2:
        one_way_km += haversine_meters(
            stops[-1]["lat"], stops[-1]["lon"],
            stops[0]["lat"], stops[0]["lon"],
        ) / 1000.0

    cum = seq.get("cum_t_s") or []
    run_s = (
        float(seq.get("cycle_run_s", 0.0))
        if closed and seq.get("cycle_run_s") is not None
        else (
            float(cum[-1])
            if cum
            else one_way_km * 1000.0 / max(spec.speed_kmh / 3.6, 0.01)
        )
    )
    open_count = int(sum(bool(v) for v in seq.get("open", [True] * len(stops))))
    j_s = run_s + open_count * spec.dwell_s
    if closed:
        cycle_s = j_s + 300.0
        cycle_km = one_way_km * (2.0 if seq.get("both_ways") else 1.0)
    else:
        cycle_s = 2.0 * j_s + 600.0
        cycle_km = 2.0 * one_way_km
    return cycle_km, cycle_s / 60.0

def _station_min_headways(
    route_sequences: list[dict[str, Any]],
    period_seq_stop_totals: Sequence[tuple[Mapping[tuple[int, int], float], float]] | None,
    vehicle_specs: Mapping[str, VehicleSpec] | None = None,
) -> dict[int, float]:
    """Минимальный headway из станционного dwell/turnback ограничения Qa."""
    result: dict[int, float] = {}
    if not period_seq_stop_totals:
        return result
    for seq_idx, seq in enumerate(route_sequences):
        rid = int(seq["route_id"])
        mode = str(seq.get("route_type_key") or "").lower()
        spec = vehicle_specs.get(mode) if vehicle_specs else None
        if spec is None:
            spec = vehicle_spec_for_route_type(mode)
        if spec.capacity <= 0:
            continue
        station_min = 0.0
        stops = seq.get("stops") or []
        for stop_totals, hours in period_seq_stop_totals:
            hours = max(float(hours), 1e-9)
            peak_rate = 0.0
            for stop_idx in range(len(stops)):
                stop_p = float(stop_totals.get((seq_idx, stop_idx), 0.0))
                peak_rate = max(peak_rate, stop_p / hours / float(spec.capacity))
            n = 60.0 - float(spec.dwell_per_pax_s) * peak_rate / 60.0
            dwell_min = (float(spec.dwell_s) + 25.0) / n if n > 6.0 else 999.0
            turnback_min = 0.0 if seq.get("closed") else float(spec.turnback_s) / 120.0
            station_min = max(station_min, dwell_min, turnback_min)
        result[rid] = max(result.get(rid, 0.0), station_min)
    return result

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
    seg_forward_totals: Mapping[tuple[int, int], float] | None = None,
    seg_reverse_totals: Mapping[tuple[int, int], float] | None = None,
    period_seq_stop_totals: Sequence[tuple[Mapping[tuple[int, int], float], float]] | None = None,
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
    shared_min_headway = _shared_capacity_min_headways(
        route_sequences, headway_min, headway_by_route
    )
    station_min_headway = _station_min_headways(route_sequences, period_seq_stop_totals, vehicle_specs)
    shared_capital_sections: set[tuple[str, str, str]] = set()
    _atomic_lines, atomic_sections = _atomic_infrastructure_sections(route_sequences)
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
        if seq.get("closed") and seq.get("both_ways"):
            fleet *= 2
        share = trips / assigned_trips if assigned_trips > 0.0 else 0.0
        capacity = spec.capacity
        one_way_km = (
            cycle_km / 2.0
            if seq.get("closed") and seq.get("both_ways")
            else (cycle_km if seq.get("closed") else cycle_km / 2.0)
        )
        capital_cost_eur = _sequence_capital_cost_eur(
            seq,
            spec,
            capex_factor,
            shared_capital_sections,
            atomic_sections,
        )

        # Пассажиро-километры и классы: по сегментам направления при наличии
        # seg_totals (Takt: segP → passengerKm/классы по nt сегмента).
        pkm = crowded_km = excess_km = severe_km = extreme_km = 0.0
        oneway_km = 0.5 * cycle_km
        max_nt = 0.0
        if seg_totals is not None:
            stops = seq["stops"]
            seg_count = (
                len(stops)
                if seq.get("closed")
                else max(0, len(stops) - 1)
            )
            seg_km = [
                haversine_meters(
                    stops[i]["lat"], stops[i]["lon"],
                    stops[(i + 1) % len(stops)]["lat"],
                    stops[(i + 1) % len(stops)]["lon"],
                ) / 1000.0
                for i in range(seg_count)
            ]
            for seg_i, km in enumerate(seg_km):
                seg_pax = float(seg_totals.get((seq.get("_seq_idx", -1), seg_i), 0.0))
                if seg_pax <= 0.0:
                    continue
                seg_forward = (
                    float(seg_forward_totals.get((seq.get("_seq_idx", -1), seg_i), 0.0))
                    if seg_forward_totals is not None
                    else 0.0
                )
                seg_reverse = (
                    float(seg_reverse_totals.get((seq.get("_seq_idx", -1), seg_i), 0.0))
                    if seg_reverse_totals is not None
                    else 0.0
                )
                if seg_forward_totals is not None or seg_reverse_totals is not None:
                    seg_pax = max(seg_forward, seg_reverse)
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
                opex_day=(
                    cycle_km * runs_day * spec.opex_per_veh_km
                    + fleet * spec.veh_cost_day
                ),
                revenue_day=revenue_day * share,
                capital_cost_eur=capital_cost_eur,
                capex_day=(
                    capital_cost_eur
                    / max(365.0 * max(float(capex_amort_years), 0.01), 1.0)
                ),
                crowding=max_nt,
                min_headway=max(
                    shared_min_headway.get(rid, 60.0 / max(spec.track_tph, 1e-6)),
                    station_min_headway.get(rid, 0.0),
                ),
                passenger_km=pkm,
                crowded_passenger_km=crowded_km,
                excess_passenger_km=excess_km,
                severe_passenger_km=severe_km,
                extreme_passenger_km=extreme_km,
            )
        )
    results.sort(key=lambda r: -r.trips)
    return results
