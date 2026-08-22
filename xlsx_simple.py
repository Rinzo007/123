"""Простые листы XLSX без предметной аналитической логики."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .support import fix_stop_coord, fmt_curv_cell
from .xlsx_helpers import cached_type_label, finish_sheet


def write_errors_sheet(wb: Any, city: str, bad: Sequence[Any]) -> None:
    ws = wb.create_sheet("Ошибки")
    headers = ["Город", "Тип", "Номер", "ID", "Ошибка", "Ссылка"]
    ws.append(headers)
    for route in bad:
        ws.append([
            city, cached_type_label(route.route_type), route.name,
            route.route_id, route.error, route.url,
        ])
    finish_sheet(ws, headers, wrap={5}, link_col=6, fixed_widths=[12, 10, 12, 8, 40, 30])


def write_excluded_sheet(
    wb: Any,
    city: str,
    excluded: Sequence[tuple[Any, str]],
) -> None:
    ws = wb.create_sheet("Отсечённые (этап 2)")
    headers = ["Город", "Тип", "Номер", "ID", "Причина", "Ссылка"]
    ws.append(headers)
    for route, reason in excluded:
        ws.append([
            city, cached_type_label(route.route_type), route.name,
            route.route_id, reason, route.url,
        ])
    finish_sheet(ws, headers, wrap={5}, link_col=6, fixed_widths=[12, 10, 12, 8, 40, 30])


def _stop_coord(rec: Mapping[str, Any], bbox: Any) -> Any:
    if rec.get("lat") is None or rec.get("lon") is None:
        return None
    return fix_stop_coord(
        {"latitude": rec["lat"], "longitude": rec["lon"]},
        rec.get("idx", 0),
        bbox,
    )


def write_unique_stops_sheet(wb: Any, unique_stops: Mapping[str, Mapping[str, Any]], bbox: Any) -> None:
    ws = wb.create_sheet("Остановки (уникальные)")
    headers = ["Название", "ID", "Типы", "Маршрутов", "Широта", "Долгота"]
    ws.append(headers)
    for rec in sorted(unique_stops.values(), key=lambda r: (str(r["name"]).lower(), str(r["id"]))):
        coord = _stop_coord(rec, bbox)
        ws.append([
            rec["name"], rec["id"] if rec["id"] is not None else "",
            ", ".join(cached_type_label(t) for t in sorted(rec["types"])),
            len(rec["routes"]), coord[0] if coord else None, coord[1] if coord else None,
        ])
    finish_sheet(ws, headers, num_fmt={5: "0.0000000", 6: "0.0000000"}, wrap={1, 3}, fixed_widths=[25, 12, 20, 10, 15, 15])


def write_stop_volumes_sheet(
    wb: Any,
    unique_stops: Mapping[str, Mapping[str, Any]],
    stop_volumes: Mapping[str, float],
    vol_radius: float | None,
    bbox: Any,
) -> None:
    radius = vol_radius if vol_radius else 500.0
    ws = wb.create_sheet("Объём застройки остановок")
    headers = ["Название", "ID", "Маршрутов", "Широта", "Долгота", f"Объём в радиусе {radius:.0f} м, тыс. м³"]
    ws.append(headers)
    rows = []
    for key, rec in unique_stops.items():
        coord = _stop_coord(rec, bbox)
        rows.append([
            rec["name"], rec["id"] if rec["id"] is not None else "", len(rec["routes"]),
            coord[0] if coord else None, coord[1] if coord else None,
            round(stop_volumes.get(key, 0.0) / 1e3, 1),
        ])
    rows.sort(key=lambda row: -(row[5] or 0))
    for row in rows:
        ws.append(row)
    finish_sheet(ws, headers, num_fmt={4: "0.0000000", 5: "0.0000000", 6: "#,##0.0"}, wrap={1}, fixed_widths=[25, 12, 10, 15, 15, 15])


def write_generated_sheet(wb: Any, generated_routes: Sequence[Mapping[str, Any]]) -> None:
    ws = wb.create_sheet("Сгенерированные маршруты")
    headers = ["№", "От", "До", "Длина, км", "Остановок", "Прямолинейность", "Объём коридора, тыс. м³", "Объём/км, тыс. м³", "Статус", "Трасса (остановки)"]
    ws.append(headers)
    for route in generated_routes:
        ws.append([
            route["n"], route["from"], route["to"], round(route["length_km"], 2), route["stops"],
            fmt_curv_cell(route["curvilinearity"]), round(route["volume"] / 1e3, 1),
            round(route["volume"] / 1e3 / route["length_km"], 1) if route["length_km"] > 0 else None,
            route["status"], route["chain"],
        ])
    finish_sheet(ws, headers, num_fmt={4: "0.00", 6: "0.00", 7: "#,##0.0", 8: "#,##0.0"}, wrap={2, 3, 10}, fixed_widths=[6, 20, 20, 10, 8, 10, 12, 12, 10, 40])


def write_heatmap_sheet(wb: Any, heatmap: Mapping[str, Any]) -> None:
    ws = wb.create_sheet("Тепловая карта")
    headers = ["№", "Широта центра", "Долгота центра", "Значение", "Доля от макс, %"]
    ws.append(headers)
    for index, cell in enumerate(heatmap["cells"], 1):
        ws.append([
            index, round((cell["lat0"] + cell["lat1"]) / 2, 6),
            round((cell["lon0"] + cell["lon1"]) / 2, 6),
            round(cell["value"], 1), round(cell["t"] * 100, 1),
        ])
    finish_sheet(ws, headers, num_fmt={2: "0.000000", 3: "0.000000", 5: "0.0"}, fixed_widths=[6, 15, 15, 12, 12])


def write_all_stops_sheet(
    wb: Any,
    *,
    city: str,
    ok: Sequence[Any],
    bbox: Any,
) -> None:
    ws = wb.create_sheet("Остановки")
    headers = ["Город", "Тип", "Номер", "ID", "№ напр.", "№ ост.", "Название", "Широта", "Долгота"]
    ws.append(headers)
    for route in ok:
        type_label = cached_type_label(route.route_type)
        for di, direction in enumerate(route.directions):
            for si, stop in enumerate(direction.stops):
                if not isinstance(stop, dict):
                    continue
                coord = fix_stop_coord(stop, si, bbox)
                ws.append([
                    city, type_label, route.name, route.route_id, di + 1, si + 1,
                    str(stop.get("name", "")), coord[0] if coord else None, coord[1] if coord else None,
                ])
    finish_sheet(ws, headers, num_fmt={8: "0.0000000", 9: "0.0000000"}, wrap={7}, fixed_widths=[12, 10, 12, 8, 8, 8, 25, 15, 15])
