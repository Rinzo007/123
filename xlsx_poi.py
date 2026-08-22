"""Листы XLSX для аналитики POI."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .xlsx_helpers import cached_type_label, finish_sheet


def _poi_sort_value(route: Any, poi_stats: Mapping[Any, Any]) -> float:
    stat = poi_stats.get(route.route_id)
    return float(stat.total_value) if stat else 0.0


def write_poi_sheets(
    wb: Any,
    *,
    city: str,
    ok: Sequence[Any],
    poi_stats: Mapping[Any, Any],
    poi_values: Mapping[str, float],
) -> None:
    """Создаёт листы «POI вдоль маршрутов» и «Ценность типов POI»."""
    ws = wb.create_sheet("POI вдоль маршрутов")
    headers = [
        "Город", "Тип", "Номер", "ID", "Точек POI",
        "Суммарное value", "Value на км",
    ]
    all_poi_types = sorted(poi_values.keys())
    headers.extend(f"{typ}: шт." for typ in all_poi_types)
    headers.append("Ссылка")
    ws.append(headers)

    for route in sorted(ok, key=lambda item: -_poi_sort_value(item, poi_stats)):
        stat = poi_stats.get(route.route_id)
        value_per_km = (
            round(stat.total_value / route.max_km, 2)
            if stat and route.max_km > 0
            else None
        )
        row = [
            city,
            cached_type_label(route.route_type),
            route.name,
            route.route_id,
            stat.count if stat else 0,
            round(stat.total_value, 1) if stat else 0.0,
            value_per_km,
        ]
        row.extend(stat.by_type.get(typ, 0) if stat else 0 for typ in all_poi_types)
        row.append(route.url)
        ws.append(row)

    num_fmt = {6: "#,##0.0", 7: "0.00"}
    num_fmt.update({i: "0" for i in range(8, 8 + len(all_poi_types))})
    fixed_widths = [12, 10, 12, 8, 10, 12, 12] + [8] * len(all_poi_types) + [30]
    finish_sheet(ws, headers, num_fmt=num_fmt, link_col=len(headers), fixed_widths=fixed_widths)

    ws2 = wb.create_sheet("Ценность типов POI")
    ws2.append(["Тип POI", "Количество", "Ценность (value)"])
    total_counts: dict[str, int] = {}
    for stat in poi_stats.values():
        for typ, count in stat.by_type.items():
            total_counts[typ] = total_counts.get(typ, 0) + count

    for typ, value in sorted(poi_values.items(), key=lambda item: -item[1]):
        ws2.append([typ, total_counts.get(typ, 0), round(value, 2)])

    finish_sheet(
        ws2,
        ["Тип POI", "Количество", "Ценность (value)"],
        num_fmt={3: "#,##0.00"},
        wrap={1},
        fixed_widths=[25, 12, 15],
    )
