"""Построитель листа «Сводка» XLSX-отчёта."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from .models import RouteData
from .support import collect_terminal_routes
from .xlsx_helpers import cached_type_label, finish_sheet, route_fully_in_bbox


def summary_type_rows(
    ok: list[RouteData],
    bbox: tuple[float, float, float, float] | None,
) -> list[list[Any]]:
    """Строки сводки с количеством маршрутов по типам транспорта."""
    rows: list[list[Any]] = [
        [
            title,
            sum(1 for route in ok if cached_type_label(route.route_type) == label),
        ]
        for title, label in (
            ("Троллейбусов", "Троллейбус"),
            ("Трамваев", "Трамвай"),
            ("Автобусов", "Автобус"),
            ("Маршруток", "Маршрутное такси"),
            ("Водных маршрутов", "Водный транспорт"),
        )
    ]

    trains_in_bbox = sum(
        1
        for route in ok
        if cached_type_label(route.route_type) == "Поезд"
        and route_fully_in_bbox(route, bbox)
    )

    return [["Поездов", trains_in_bbox], *rows]


def summary_rows(
    *,
    city: str,
    city_title: str,
    routes: Sequence[RouteData],
    ok: list[RouteData],
    bad: list[RouteData],
    ideas: list[RouteData],
    skipped_inactive: int,
    curv_limit: float,
    minlen_limit: float,
    radius_limit: float,
    cut_curv: int,
    cut_len: int,
    cut_radius: int,
    dedup_removed: Sequence[Mapping[str, Any]] | None,
    dedup_analysis: Mapping[str, Any] | None,
    net_metrics: Mapping[str, Any] | None,
    net_density: Any,
    kml_routes: Sequence[RouteData] | None,
    unique_stops: Mapping[str, Mapping[str, Any]] | None,
    generated_routes: Sequence[Mapping[str, Any]] | None,
    bbox: tuple[float, float, float, float] | None,
    ghs_stats: Mapping[Any, Any] | None,
    ghs_meta: Mapping[str, Any] | None,
    poi_buffer_m: float,
    poi_stats: Mapping[Any, Any] | None,
    built_s_stats: Mapping[Any, Any] | None,
    built_s_meta: Mapping[str, Any] | None,
    overture_stats: Mapping[Any, Any] | None,
    overture_meta: Mapping[str, Any] | None,
) -> list[list[Any]]:
    """Все строки листа «Сводка»."""
    rows: list[list[Any]] = [
        ["Город", city_title],
        ["Slug", city],
        ["Дата", datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")],
        ["Маршрутов в обработке", len(routes)],
        ["Успешно (в отчёте)", len(ok)],
        ["Ошибок", len(bad)],
        ["Идей пассажиров", len(ideas)],
        ["Пропущено неактивных (--active-only)", skipped_inactive],
        ["Отсечено: криволинейность", cut_curv],
        ["Отсечено: длина", cut_len],
        ["Отсечено: радиус", cut_radius],
        [
            "Дедупликация: удалено направлений",
            len(dedup_removed) if dedup_removed else 0,
        ],
        [
            "Дедупликация: Км до",
            dedup_analysis.get("km_coef", "") if dedup_analysis else "",
        ],
        [
            "Маршрутный коэффициент Км",
            net_metrics.get("km_coef", "") if net_metrics else "",
        ],
        [
            "Суммарная длина трасс, км",
            net_metrics.get("total_km", "") if net_metrics else "",
        ],
        [
            "Длина уникальной сети, км",
            net_metrics.get("unique_km", "") if net_metrics else "",
        ],
        ["Плотность сети, км/км²", net_density],
        ["Макс. коэфф. непрямолинейности (0=без лимита)", curv_limit],
        ["Мин. длина маршрута, км (0=без лимита)", minlen_limit],
        ["Радиус от центра, км (0=без лимита)", radius_limit],
        [
            "Маршрутов на карте (KML)",
            len(kml_routes) if kml_routes is not None else 0,
        ],
        [
            "Остановок уникальных",
            len(unique_stops) if unique_stops is not None else 0,
        ],
        ["Конечных", len(collect_terminal_routes(ok))],
        [
            "Сгенерировано маршрутов (Якимов)",
            len(generated_routes) if generated_routes else "",
        ],
    ]

    rows.extend(summary_type_rows(ok, bbox))
    rows.extend(
        [
            ["GHS: буфер, м", ghs_meta.get("buffer_m", "") if ghs_meta else ""],
            [
                "GHS: суммарный объём, тыс. м³",
                round(sum(stat.volume_m3 for stat in ghs_stats.values()) / 1e3, 1)
                if ghs_stats
                else "",
            ],
            ["POI: буфер, м", poi_buffer_m if poi_stats else ""],
            [
                "POI: суммарное value",
                round(sum(stat.total_value for stat in poi_stats.values()), 1)
                if poi_stats
                else "",
            ],
        ]
    )

    if built_s_meta:
        rows.append(["GHS-BUILT-S: буфер, м", built_s_meta.get("buffer_m", "")])

    if built_s_stats:
        total_surface = sum(stat.surface_m2 for stat in built_s_stats.values())
        rows.append(
            [
                "GHS-BUILT-S: суммарная поверхность, тыс. м²",
                round(total_surface / 1e3, 1),
            ]
        )

    if overture_meta:
        rows.append(["Overture: буфер, м", overture_meta.get("buffer_m", "")])

    if overture_stats:
        total_area = sum(stat.total_area_m2 for stat in overture_stats.values())
        rows.append(
            ["Overture: суммарная площадь, тыс. м²", round(total_area / 1e3, 1)]
        )

    return rows


def write_summary_sheet(wb: Any, rows: Sequence[Sequence[Any]]) -> None:
    """Записывает лист «Сводка»."""
    ws = wb.active
    assert ws is not None
    ws.title = "Сводка"
    ws.append(["Параметр", "Значение"])

    for row in rows:
        ws.append(list(row))

    finish_sheet(ws, ["Параметр", "Значение"], wrap={2}, fixed_widths=[25, 35])


__all__ = ["summary_rows", "summary_type_rows", "write_summary_sheet"]
