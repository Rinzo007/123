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
        if cross > 1e-4:
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

def _sequence_period_headway(
    seq: Mapping[str, Any],
    period_index: int,
    default_headway_min: float,
    headway_by_route: Mapping[int, float] | None,
) -> float:
    raw = seq.get("headways")
    if isinstance(raw, (list, tuple)) and period_index < len(raw):
        try:
            hv = float(raw[period_index])
            if hv > 0.0 and math.isfinite(hv):
                return hv
        except (TypeError, ValueError):
            pass
    rid = int(seq["route_id"])
    if headway_by_route is not None and rid in headway_by_route:
        return float(headway_by_route[rid])
    return float(default_headway_min)


def _shared_capacity_min_headways(
    route_sequences: list[dict[str, Any]],
    default_headway_min: float,
    headway_by_route: Mapping[int, float] | None,
    atomic_sections: dict[tuple[str, str, str], set[int]] | None = None,
) -> dict[int, float]:
    """Ga: shared track residual capacity, evaluated for every period."""
    section_lines = atomic_sections if atomic_sections is not None else _atomic_infrastructure_sections(route_sequences)[0]
    result: dict[int, float] = {
        int(seq["route_id"]): 60.0 / max(float(vehicle_spec_for_route_type(seq.get("route_type_key", "bus")).track_tph), 1e-9)
        for seq in route_sequences
    }
    by_route = {int(seq["route_id"]): seq for seq in route_sequences}
    for key, lines in section_lines.items():
        if len(lines) < 2:
            continue
        mode = key[0]
        limit = float(vehicle_spec_for_route_type(mode).track_tph)
        for period_index in range(5):
            line_freq: dict[int, float] = {}
            for seq_idx in lines:
                seq = by_route.get(int(seq_idx))
                if seq is None:
                    continue
                h = _sequence_period_headway(seq, period_index, default_headway_min, headway_by_route)
                line_freq[int(seq["route_id"])] = 60.0 / max(h, 1e-9) if h > 0.0 else 0.0
            for rid in list(line_freq):
                residual = limit - sum(freq for other, freq in line_freq.items() if other != rid)
                minimum = 60.0 / residual if residual > 0.01 else math.inf
                result[rid] = max(result.get(rid, 0.0), minimum)
    return result
def _geometry_point_key(point: Any) -> str:
    lon = float(point[0]); lat = float(point[1])
    # JS Math.round semantics (including negative coordinates).
    lon_i = math.floor(lon * 1e5 + 0.5)
    lat_i = math.floor(lat * 1e5 + 0.5)
    return f"{lon_i},{lat_i}"


def _geometry_edge_key(a: Any, b: Any) -> tuple[str, str]:
    ka = _geometry_point_key(a); kb = _geometry_point_key(b)
    return (ka, kb) if ka < kb else (kb, ka)


def _geometry_reuse_edges(
    route_sequences: Sequence[Mapping[str, Any]],
) -> set[tuple[str, str]]:
    """La(): edges of decoded legs that already have an owned geometry."""
    reused: set[tuple[str, str]] = set()
    for seq in route_sequences:
        legs = seq.get("geometry_legs")
        if not isinstance(legs, (list, tuple)):
            continue
        gaps = seq.get("gaps") or []
        built = seq.get("built_segs")
        for seg_i, leg in enumerate(legs):
            if seg_i < len(gaps) and bool(gaps[seg_i]):
                continue
            if isinstance(built, (list, tuple)) and seg_i < len(built) and built[seg_i] is False:
                continue
            if not isinstance(leg, (list, tuple)) or len(leg) < 2:
                continue
            for a, b in zip(leg, leg[1:]):
                try:
                    reused.add(_geometry_edge_key(a, b))
                except (TypeError, ValueError, IndexError):
                    continue
    return reused

def _sequence_capital_cost_eur(
    seq: Mapping[str, Any],
    spec: VehicleSpec,
    capex_factor: float,
    shared_sections: set[tuple[str, str, str]] | None = None,
    atomic_sections: Mapping[tuple[int, int], list[tuple[tuple[str, str, str], float]]] | None = None,
    geometry_reuse_edges: set[tuple[str, str]] | None = None,
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
        fixed_full_build = False
        if isinstance(fixed_leg, Mapping):
            if bool(fixed_leg.get("rebuild")) and fixed_leg.get("rebuildCostM") is not None:
                total += max(0.0, float(fixed_leg["rebuildCostM"])) * 1e6 * float(capex_factor)
                continue
            if fixed_leg.get("authored") is False and fixed_leg.get("costM") is not None:
                total += max(0.0, float(fixed_leg["costM"])) * 1e6 * float(capex_factor)
                continue
            fixed_full_build = bool(fixed_leg.get("buildRanges"))
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
        geometry_legs = seq.get("geometry_legs")
        segment_lengths_m = seq.get("segment_lengths_m")
        if (
            geometry_reuse_edges is not None
            and isinstance(geometry_legs, (list, tuple))
            and i < len(geometry_legs)
            and isinstance(geometry_legs[i], (list, tuple))
            and len(geometry_legs[i]) >= 2
        ):
            leg = geometry_legs[i]
            total_m = 0.0
            reused_m = 0.0
            for a, b in zip(leg, leg[1:]):
                try:
                    edge_m = haversine_meters(float(a[1]), float(a[0]), float(b[1]), float(b[0]))
                except (TypeError, ValueError, IndexError):
                    continue
                total_m += edge_m
                if _geometry_edge_key(a, b) in geometry_reuse_edges:
                    reused_m += edge_m
            if total_m > 0.0:
                if isinstance(segment_lengths_m, (list, tuple)) and i < len(segment_lengths_m):
                    try:
                        segment_m = max(0.0, float(segment_lengths_m[i]))
                    except (TypeError, ValueError):
                        segment_m = total_m
                else:
                    segment_m = total_m
                distance_cost = (
                    segment_m / 1000.0
                    * cost_per_km_eur
                    * multiplier
                    * float(capex_factor)
                )
                total += distance_cost * max(0.0, 1.0 - reused_m / total_m)
                continue
        pieces = (
            []
            if fixed_full_build
            else atomic_sections.get((int(seq.get("_seq_idx", -1)), i), [])
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
    default_headway_min: float = 10.0,
    headway_by_route: Mapping[int, float] | None = None,
) -> dict[int, float]:
    """Qa: station dwell/turnback limit for each service period."""
    result: dict[int, float] = {}
    if not period_seq_stop_totals:
        return result
    for seq_idx, seq in enumerate(route_sequences):
        rid = int(seq["route_id"])
        mode = str(seq.get("route_type_key") or "").lower()
        spec = vehicle_specs.get(mode) if vehicle_specs else None
        if spec is None:
            spec = vehicle_spec_for_route_type(mode)
        station_min = 0.0
        for period_index, (stop_totals, hours) in enumerate(period_seq_stop_totals):
            h = _sequence_period_headway(seq, period_index, default_headway_min, headway_by_route)
            if h <= 0.0:
                continue
            period_runs = max(float(hours), 1e-9) * 60.0 / h
            direction_factor = 1.0 if bool(seq.get("closed")) and not bool(seq.get("both_ways")) else 2.0
            pax_per_train = 0.0
            if period_runs > 0.0:
                for stop_idx in range(len(seq.get("stops") or [])):
                    stop_p = float(stop_totals.get((seq_idx, stop_idx), 0.0))
                    pax_per_train = max(pax_per_train, stop_p / (period_runs * direction_factor))
            n = 60.0 - float(spec.dwell_per_pax_s) * pax_per_train / 60.0
            dwell_min = (float(spec.dwell_s) + 25.0) / n if n > 6.0 else 999.0
            turnback_min = 0.0 if bool(seq.get("closed")) else float(spec.turnback_s) / 120.0
            station_min = max(station_min, dwell_min, turnback_min)
        result[rid] = max(result.get(rid, 0.0), station_min)
    return result

def _period_service_metrics(
    seq: Mapping[str, Any],
    spec: VehicleSpec,
    period_seq_stop_totals: Sequence[tuple[Mapping[tuple[int, int], float], float]] | None,
    default_headway_min: float,
    headway_by_route: Mapping[int, float] | None,
) -> tuple[float, float, float]:
    """Точные для JS cn метрики: max fleet, vehicle km/day и variable OPEX."""
    stops = seq.get("stops") or []
    n = len(stops)
    if n < 2:
        return 0.0, 0.0, 0.0
    closed = bool(seq.get("closed"))
    one_way_km = sum(
        haversine_meters(stops[i]["lat"], stops[i]["lon"], stops[(i + 1) % n]["lat"], stops[(i + 1) % n]["lon"]) / 1000.0
        for i in range(n if closed else n - 1)
    )
    cum = seq.get("cum_t_s") or []
    run_s = float(seq.get("cycle_run_s", 0.0)) if seq.get("cycle_run_s") is not None else (
        float(cum[-1]) if cum else one_way_km * 1000.0 / max(float(spec.speed_kmh) / 3.6, 0.01)
    )
    open_count = int(sum(bool(v) for v in seq.get("open", [True] * n)))
    j_s = run_s + open_count * float(spec.dwell_s)
    base_cycle_s = j_s + 300.0 if closed else 2.0 * j_s + 600.0
    direction_factor = 1.0 if closed and not bool(seq.get("both_ways")) else 2.0
    fleet_factor = 2.0 if closed and bool(seq.get("both_ways")) else 1.0
    fleet_max = 0.0
    vehicle_km_day = 0.0
    periods = period_seq_stop_totals or (({}, 24.0),)
    seq_idx = int(seq.get("_seq_idx", -1))
    for period_index, (stop_totals, hours) in enumerate(periods):
        h = _sequence_period_headway(seq, period_index, default_headway_min, headway_by_route)
        if h <= 0.0:
            continue
        period_runs = max(float(hours), 1e-9) * 60.0 / h
        dwell_extra_s = 0.0
        if stop_totals:
            total_stop_pax = sum(max(0.0, float(stop_totals.get((seq_idx, si), 0.0))) for si in range(n))
            dwell_runs = direction_factor * period_runs
            if dwell_runs > 0.0:
                dwell_extra_s = float(spec.dwell_per_pax_s) * total_stop_pax / dwell_runs
        it_s = (1.0 if closed else 2.0) * dwell_extra_s
        fleet = math.ceil((base_cycle_s + it_s) / (h * 60.0)) * fleet_factor
        fleet_max = max(fleet_max, float(fleet))
        vehicle_km_day += direction_factor * period_runs * one_way_km
    return fleet_max, vehicle_km_day, vehicle_km_day * float(spec.opex_per_veh_km)

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
    _atomic_lines, atomic_sections = _atomic_infrastructure_sections(route_sequences)
    shared_min_headway = _shared_capacity_min_headways(
        route_sequences, headway_min, headway_by_route, atomic_sections=_atomic_lines
    )
    station_min_headway = _station_min_headways(route_sequences, period_seq_stop_totals, vehicle_specs)
    shared_capital_sections: set[tuple[str, str, str]] = set()
    geometry_reuse_edges = _geometry_reuse_edges(route_sequences)
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
        hv = _sequence_period_headway(seq, 0, headway_min, headway_by_route)
        runs_day = 1440.0 / max(float(hv), 1e-6)
        cycle_km, cycle_min = _route_cycle(seq, spec)
        period_fleet, vehicle_km_day, variable_opex_day = _period_service_metrics(
            seq, spec, period_seq_stop_totals, headway_min, headway_by_route
        )
        fleet = int(period_fleet)
        capacity = float(seq.get("capacity", spec.capacity))
        occupancy_factor = capacity / max(float(spec.capacity), 1.0)
        opex_day = (variable_opex_day + fleet * float(spec.veh_cost_day)) * occupancy_factor
        share = trips / assigned_trips if assigned_trips > 0.0 else 0.0
        capital_cost_eur = _sequence_capital_cost_eur(
            seq,
            spec,
            capex_factor,
            shared_capital_sections,
            atomic_sections,
            geometry_reuse_edges,
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
                veh_km_day=vehicle_km_day,
                opex_day=opex_day,
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
