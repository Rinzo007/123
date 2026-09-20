"""Сборка итогового результата расчёта из накопленных агрегатов."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from decimal import Decimal, ROUND_HALF_UP


def round_half_up(value: float, digits: int = 0) -> float:
    """Round using decimal half-up semantics, matching the legacy helper."""
    quantum = Decimal("1").scaleb(-int(digits))
    return float(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP))
from ..base.models import (
    FlowResult,
    LineResult,
    PeriodFlow,
    RouteDirectionFlow,
    RouteFlowResult,
    RouteStopData,
    StopFlowResult,
)


# ===== Маршруты =====


def _route_direction_stops(seq: dict[str, Any]) -> tuple[RouteStopData, ...]:
    """Остановки одного направления в виде ``RouteStopData``."""
    return tuple(
        RouteStopData(
            stop_name=s["name"],
            lat=s["lat"],
            lon=s["lon"],
            position=s["position"],
            stop_id=s.get("id"),
        )
        for s in seq["stops"]
    )


def _route_directions(
    rid: int,
    route_stop_sequences: Sequence[dict[str, Any]],
    dir_totals: Mapping[tuple[int, int], float],
) -> tuple[RouteDirectionFlow, ...]:
    """Направления одного маршрута с округлёнными пассажиропотоками."""
    directions: list[RouteDirectionFlow] = []
    for seq in route_stop_sequences:
        if seq["route_id"] != rid:
            continue
        dir_total = dir_totals.get((rid, seq["di"]), 0.0)
        directions.append(
            RouteDirectionFlow(
                direction_name=seq["direction_name"],
                total_passengers=round_half_up(dir_total, 1),
                route_stops=_route_direction_stops(seq),
            )
        )
    return tuple(directions)


def _aggregate_route_stop_flows(
    rid: int,
    route_stop_totals: Mapping[tuple[int, int], Mapping[str, float]],
) -> dict[str, float]:
    """Суммирует потоки остановок маршрута по всем его направлениям.

    Возвращает округлённый до 1 знака словарь ``{имя остановки: поездок}``.
    """
    sf: dict[str, float] = {}
    for (r_rid, _r_di), stops_data in route_stop_totals.items():
        if r_rid != rid:
            continue
        for sname, sval in stops_data.items():
            sf[sname] = sf.get(sname, 0.0) + sval
    return {k: round_half_up(v, 1) for k, v in sf.items()}


def _build_route_flows(
    route_totals: Mapping[int, float],
    dir_totals: Mapping[tuple[int, int], float],
    route_types_map: Mapping[int, str],
    route_names: Mapping[int, str],
    route_stop_totals: Mapping[tuple[int, int], Mapping[str, float]],
    route_stop_sequences: Sequence[dict[str, Any]],
) -> tuple[RouteFlowResult, ...]:
    """Собирает маршруты с направлениями и потоками остановок (по убыванию)."""
    route_flows: list[RouteFlowResult] = []
    for rid, total in sorted(route_totals.items(), key=lambda x: -x[1]):
        route_flows.append(
            RouteFlowResult(
                route_id=rid,
                route_name=route_names.get(rid, str(rid)),
                route_type=route_types_map.get(rid, ""),
                total_passengers=round_half_up(total, 1),
                directions=_route_directions(rid, route_stop_sequences, dir_totals),
                stop_flows=_aggregate_route_stop_flows(rid, route_stop_totals),
            )
        )
    return tuple(route_flows)


# ===== Остановки =====


def _stop_matches(
    s: Mapping[str, Any], *, stop_id: Any, lat: float, lon: float
) -> bool:
    """Совпадает ли остановка маршрута с агрегированной записью остановки.

    Матч либо по ``stop_id``, либо по координатам с точностью 1e-5.
    """
    if stop_id is not None and s["id"] == stop_id:
        return True
    return abs(s["lat"] - lat) < 1e-5 and abs(s["lon"] - lon) < 1e-5


def _seq_route_name_at_stop(
    seq: Mapping[str, Any], *, stop_id: Any, lat: float, lon: float
) -> str | None:
    """Имя маршрута направления, если оно обслуживает остановку; иначе ``None``.

    Возвращает на первой совпавшей остановке направления — как в исходном
    ``break``: каждое направление даёт не более одного имени.
    """
    for s in seq["stops"]:
        if _stop_matches(s, stop_id=stop_id, lat=lat, lon=lon):
            return f"{seq['route_type']} №{seq['route_name']}"
    return None


def _route_names_at_stop(
    route_stop_sequences: Sequence[dict[str, Any]],
    *,
    stop_id: Any,
    lat: float,
    lon: float,
) -> list[str]:
    """Уникальные имена маршрутов, проходящих через агрегированную остановку."""
    names: list[str] = []
    for seq in route_stop_sequences:
        rname = _seq_route_name_at_stop(seq, stop_id=stop_id, lat=lat, lon=lon)
        if rname is None or rname in names:
            continue
        names.append(rname)
    return names


def _build_stop_flow(
    key: str,
    data: Mapping[str, Any],
    route_stop_sequences: Sequence[dict[str, Any]],
) -> StopFlowResult | None:
    """Собирает одну запись остановки; None, если поток < 0.01 поездок."""
    total = data["boardings"] + data["alightings"]
    if total < 0.01:
        return None
    lat = data["lat"]
    lon = data["lon"]
    routes = tuple(
        _route_names_at_stop(
            route_stop_sequences,
            stop_id=data["stop_id"],
            lat=lat,
            lon=lon,
        )
    )
    return StopFlowResult(
        name=data["name"] or key,
        lat=lat,
        lon=lon,
        boardings=round_half_up(data["boardings"], 1),
        alightings=round_half_up(data["alightings"], 1),
        total_flow=round_half_up(total, 1),
        routes=routes,
    )


def _build_stop_flows(
    stop_totals: Mapping[str, dict[str, Any]],
    route_stop_sequences: Sequence[dict[str, Any]],
) -> tuple[StopFlowResult, ...]:
    """Собирает остановки с потоком >= 0.01, по убыванию суммарного потока."""
    sorted_stops = sorted(
        stop_totals.items(),
        key=lambda x: x[1]["boardings"] + x[1]["alightings"],
        reverse=True,
    )
    result: list[StopFlowResult] = []
    for key, data in sorted_stops:
        entry = _build_stop_flow(key, data, route_stop_sequences)
        if entry is not None:
            result.append(entry)
    return tuple(result)


# ===== Округление результатов линий и периодов =====


def _round_line_result(r: LineResult) -> LineResult:
    """Округляет все поля одной ``LineResult`` до согласованных знаков."""
    return LineResult(
        route_id=r.route_id,
        route_name=r.route_name,
        mode=r.mode,
        trips=round_half_up(r.trips, 1),
        cycle_km=round_half_up(r.cycle_km, 2),
        cycle_min=round_half_up(r.cycle_min, 1),
        fleet=round_half_up(r.fleet, 1),
        veh_km_day=round_half_up(r.veh_km_day, 1),
        opex_day=round_half_up(r.opex_day, 1),
        revenue_day=round_half_up(r.revenue_day, 1),
        capital_cost_eur=round_half_up(r.capital_cost_eur, 1),
        capex_day=round_half_up(r.capex_day, 1),
        crowding=round_half_up(r.crowding, 2),
        min_headway=round_half_up(r.min_headway, 1),
        passenger_km=round_half_up(r.passenger_km, 1),
        crowded_passenger_km=round_half_up(r.crowded_passenger_km, 1),
        excess_passenger_km=round_half_up(r.excess_passenger_km, 1),
        severe_passenger_km=round_half_up(r.severe_passenger_km, 1),
        extreme_passenger_km=round_half_up(r.extreme_passenger_km, 1),
    )


def _round_period_flow(p: PeriodFlow) -> PeriodFlow:
    """Округляет все числовые поля одной ``PeriodFlow``."""
    return PeriodFlow(
        key=p.key,
        label=p.label,
        total_trips=round_half_up(p.total_trips, 1),
        assigned_trips=round_half_up(p.assigned_trips, 1),
        car_trips=round_half_up(p.car_trips, 1),
        walk_trips=round_half_up(p.walk_trips, 1),
        two_wheel_trips=round_half_up(p.two_wheel_trips, 1),
        rest_trips=round_half_up(p.rest_trips, 1),
    )


# ===== Главная точка входа =====


def assemble_flow_result(
    *,
    route_totals: Mapping[int, float],
    dir_totals: Mapping[tuple[int, int], float],
    route_types_map: Mapping[int, str],
    route_names: Mapping[int, str],
    route_stop_totals: Mapping[tuple[int, int], Mapping[str, float]],
    stop_totals: Mapping[str, dict[str, Any]],
    route_stop_sequences: list[dict[str, Any]],
    total_trips: float,
    assigned_trips: float,
    intrazonal_trips: float,
    car_trips: float = 0.0,
    walk_trips: float = 0.0,
    two_wheel_trips: float = 0.0,
    rest_trips: float = 0.0,
    period_flows: tuple[PeriodFlow, ...] = (),
    line_results: tuple[LineResult, ...] = (),
) -> FlowResult:
    """Собирает маршруты и остановки с потоками в ``FlowResult``."""
    route_flows = _build_route_flows(
        route_totals,
        dir_totals,
        route_types_map,
        route_names,
        route_stop_totals,
        route_stop_sequences,
    )
    stop_flows = _build_stop_flows(stop_totals, route_stop_sequences)
    raw_revenue_day = sum(r.revenue_day for r in line_results)
    raw_opex_day = sum(r.opex_day for r in line_results)
    rounded_line_results = tuple(_round_line_result(r) for r in line_results)

    return FlowResult(
        route_flows=route_flows,
        stop_flows=stop_flows,
        total_trips=total_trips,
        assigned_trips=round_half_up(assigned_trips, 1),
        routes_served=len(route_totals),
        intrazonal_trips=round_half_up(intrazonal_trips, 1),
        car_trips=round_half_up(car_trips, 1),
        walk_trips=round_half_up(walk_trips, 1),
        two_wheel_trips=round_half_up(two_wheel_trips, 1),
        rest_trips=round_half_up(rest_trips, 1),
        period_flows=tuple(_round_period_flow(p) for p in period_flows),
        line_results=rounded_line_results,
        revenue_day=round_half_up(
            sum(r.revenue_day for r in rounded_line_results), 1
        ),
        opex_day=round_half_up(sum(r.opex_day for r in rounded_line_results), 1),
        capital_cost_eur=round_half_up(sum(r.capital_cost_eur for r in rounded_line_results), 1),
        capex_day=round_half_up(sum(r.capex_day for r in rounded_line_results), 1),
        fleet_total=round_half_up(sum(r.fleet for r in rounded_line_results), 1),
        raw_assigned_trips=float(assigned_trips),
        raw_car_trips=float(car_trips),
        raw_walk_trips=float(walk_trips),
        raw_two_wheel_trips=float(two_wheel_trips),
        raw_rest_trips=float(rest_trips),
        raw_revenue_day=float(raw_revenue_day),
        raw_opex_day=float(raw_opex_day),
    )