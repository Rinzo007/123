"""Листы XLSX-экспорта для пассажиропотока.

Три листа:
- «Пассажиропоток — маршруты»: объём по маршрутам, по направлениям, по остановкам.
- «Пассажиропоток — остановки»: посадки/высадки на каждой уникальной остановке.
- «Линии»: KPI линий (пробег, парк, opex/revenue, capex день, заполнение).
"""

from __future__ import annotations

from typing import Any

from ..passenger_flow import FlowResult
from .helpers import finish_sheet, safe_append

_ROUTES_HEADERS = [
    "Город",
    "Тип",
    "Номер",
    "ID",
    "Пассажиров (всего)",
    "Пассажиров (Напр. 1)",
    "Пассажиров (Напр. 2)",
    "Остановок",
]

_STOPS_HEADERS = [
    "Город",
    "Остановка",
    "Широта",
    "Долгота",
    "Посадок",
    "Высадок",
    "Всего пассажиров",
    "Маршруты",
]

_LINES_HEADERS = [
    "Город",
    "Тип",
    "Номер",
    "ID",
    "Пассажиров/сутки",
    "Цикл, км",
    "Время цикла, мин",
    "Пробег/сутки, км",
    "Парк, ед.",
    "Opex, €/сутки",
    "Выручка, €/сутки",
    "CapEx, €/сутки",
    "Заполнение, %",
    "Интервал, мин",
]

_ROUTES_NUM_FMT = {
    5: "#,##0",
    6: "#,##0",
    7: "#,##0",
    8: "0",
}

_STOPS_NUM_FMT = {
    3: "0.000000",
    4: "0.000000",
    5: "#,##0",
    6: "#,##0",
    7: "#,##0",
}

_LINES_NUM_FMT = {
    5: "#,##0",
    6: "0.0",
    7: "0.0",
    8: "#,##0",
    9: "0",
    10: "0.0",
    11: "0.0",
    12: "0.0",
    13: "0.0",
    14: "0.0",
}


def write_routes_flow_sheet(
    wb: Any,
    city: str,
    flow: FlowResult,
) -> None:
    """Записывает лист «Пассажиропоток — маршруты»."""
    ws = wb.create_sheet("Поток — маршруты")
    safe_append(ws, _ROUTES_HEADERS)

    for rf in flow.route_flows:
        dir1 = rf.directions[0].total_passengers if len(rf.directions) > 0 else 0.0
        dir2 = rf.directions[1].total_passengers if len(rf.directions) > 1 else 0.0
        n_stops = max(
            (len(d.route_stops) for d in rf.directions), default=0
        )
        safe_append(ws, [
            city,
            rf.route_type,
            rf.route_name,
            rf.route_id,
            round(rf.total_passengers),
            round(dir1),
            round(dir2),
            n_stops,
        ])

    finish_sheet(
        ws,
        _ROUTES_HEADERS,
        num_fmt=_ROUTES_NUM_FMT,
        fixed_widths=[10, 14, 18, 10, 16, 16, 16, 10],
    )


def write_stops_flow_sheet(
    wb: Any,
    city: str,
    flow: FlowResult,
) -> None:
    """Записывает лист «Пассажиропоток — остановки»."""
    ws = wb.create_sheet("Поток — остановки")
    safe_append(ws, _STOPS_HEADERS)

    for sf in flow.stop_flows:
        lat = round(sf.lat, 6) if sf.lat is not None else ""
        lon = round(sf.lon, 6) if sf.lon is not None else ""
        routes_str = "; ".join(sf.routes[:5])
        if len(sf.routes) > 5:
            routes_str += f" (+{len(sf.routes) - 5})"
        safe_append(ws, [
            city,
            sf.name,
            lat,
            lon,
            round(sf.boardings),
            round(sf.alightings),
            round(sf.total_flow),
            routes_str,
        ])

    finish_sheet(
        ws,
        _STOPS_HEADERS,
        num_fmt=_STOPS_NUM_FMT,
        fixed_widths=[10, 25, 12, 12, 12, 12, 16, 50],
        wrap={8},
    )


def write_lines_kpi_sheet(
    wb: Any,
    city: str,
    flow: FlowResult,
) -> None:
    """Записывает лист «Линии» с эксплуатационными KPI."""
    ws = wb.create_sheet("Линии")
    safe_append(ws, _LINES_HEADERS)

    for lr in flow.line_results:
        crowding_pct = lr.crowding * 100.0 if lr.crowding else 0.0
        safe_append(ws, [
            city,
            lr.mode,
            lr.route_name,
            lr.route_id,
            round(lr.trips),
            lr.cycle_km,
            lr.cycle_min,
            lr.veh_km_day,
            lr.fleet,
            lr.opex_day,
            lr.revenue_day,
            lr.capex_day,
            crowding_pct,
            lr.min_headway,
        ])

    finish_sheet(
        ws,
        _LINES_HEADERS,
        num_fmt=_LINES_NUM_FMT,
        fixed_widths=[10, 14, 18, 10, 16, 12, 14, 16, 12, 12, 14, 12, 12, 12],
    )


__all__ = [
    "write_lines_kpi_sheet",
    "write_routes_flow_sheet",
    "write_stops_flow_sheet",
]
