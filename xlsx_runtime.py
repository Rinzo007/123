"""Оркестрация XLSX-экспорта.

Конкретные листы отчёта находятся в специализированных модулях
``xlsx_summary``, ``xlsx_routes``, ``xlsx_poi``, ``xlsx_dedup`` и
``xlsx_simple``. Этот модуль отвечает только за создание книги и порядок
вызова writer-функций.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .models import RouteData
from .xlsx_dedup import write_dedup_sheet
from .xlsx_helpers import network_density
from .xlsx_poi import write_poi_sheets
from .xlsx_routes import write_routes_sheet
from .xlsx_simple import (
    write_all_stops_sheet,
    write_errors_sheet,
    write_excluded_sheet,
    write_generated_sheet,
    write_heatmap_sheet,
    write_stop_volumes_sheet,
    write_unique_stops_sheet,
)
from .xlsx_summary import summary_rows, write_summary_sheet

logger = logging.getLogger("wikiroutes.xlsx")


# Legacy aliases kept for code that imports private writer names from the runtime.
_write_summary_sheet = write_summary_sheet
_write_routes_sheet = write_routes_sheet
_write_errors_sheet = write_errors_sheet
_write_excluded_sheet = write_excluded_sheet
_write_unique_stops_sheet = write_unique_stops_sheet
_write_stop_volumes_sheet = write_stop_volumes_sheet
_write_poi_sheets = write_poi_sheets
_write_dedup_sheet = write_dedup_sheet
_write_generated_sheet = write_generated_sheet
_write_heatmap_sheet = write_heatmap_sheet
_write_all_stops_sheet = write_all_stops_sheet


def build_xlsx(
    routes: Sequence[RouteData],
    city: str,
    city_title: str,
    path: str | Path,
    curv_limit: float = 0.0,
    minlen_limit: float = 0.0,
    radius_limit: float = 0.0,
    cut_curv: int = 0,
    cut_len: int = 0,
    cut_radius: int = 0,
    skipped_inactive: int = 0,
    kml_routes: Sequence[RouteData] | None = None,
    unique_stops: Mapping[str, Mapping[str, Any]] | None = None,
    excluded_stage2: Sequence[tuple[RouteData, str]] | None = None,
    heatmap: Mapping[str, Any] | None = None,
    include_stops: bool = False,
    bbox: tuple[float, float, float, float] | None = None,
    ghs_stats: Mapping[Any, Any] | None = None,
    ghs_meta: Mapping[str, Any] | None = None,
    ghs_dir_stats: Mapping[Any, Any] | None = None,
    poi_stats: Mapping[Any, Any] | None = None,
    poi_values: Mapping[str, float] | None = None,
    poi_buffer_m: float = 0.0,
    poi_dir_stats: Mapping[Any, Any] | None = None,
    dedup_removed: Sequence[Mapping[str, Any]] | None = None,
    dedup_analysis: Mapping[str, Any] | None = None,
    net_metrics: Mapping[str, Any] | None = None,
    generated_routes: Sequence[Mapping[str, Any]] | None = None,
    built_s_stats: Mapping[Any, Any] | None = None,
    built_s_meta: Mapping[str, Any] | None = None,
    built_s_dir_stats: Mapping[Any, Any] | None = None,
    overture_stats: Mapping[Any, Any] | None = None,
    overture_meta: Mapping[str, Any] | None = None,
    overture_dir_stats: Mapping[Any, Any] | None = None,
    stop_volumes: Mapping[str, float] | None = None,
    vol_radius: float | None = None,
) -> str | Path | None:
    """Создаёт XLSX-отчёт и возвращает путь к нему."""
    try:
        from openpyxl import Workbook
    except ImportError:
        logger.warning("XLSX пропущен: pip install openpyxl")
        return None

    ok = [route for route in routes if not route.error and route.directions]
    bad = [route for route in routes if route.error]
    ideas = [route for route in routes if route.is_idea and not route.error]

    wb = Workbook()

    write_summary_sheet(
        wb,
        summary_rows(
            city=city,
            city_title=city_title,
            routes=routes,
            ok=ok,
            bad=bad,
            ideas=ideas,
            skipped_inactive=skipped_inactive,
            curv_limit=curv_limit,
            minlen_limit=minlen_limit,
            radius_limit=radius_limit,
            cut_curv=cut_curv,
            cut_len=cut_len,
            cut_radius=cut_radius,
            dedup_removed=dedup_removed,
            dedup_analysis=dedup_analysis,
            net_metrics=net_metrics,
            net_density=network_density(net_metrics, bbox),
            kml_routes=kml_routes,
            unique_stops=unique_stops,
            generated_routes=generated_routes,
            bbox=bbox,
            ghs_stats=ghs_stats,
            ghs_meta=ghs_meta,
            poi_buffer_m=poi_buffer_m,
            poi_stats=poi_stats,
            built_s_stats=built_s_stats,
            built_s_meta=built_s_meta,
            overture_stats=overture_stats,
            overture_meta=overture_meta,
        ),
    )

    if kml_routes:
        write_routes_sheet(
            wb,
            city=city,
            kml_routes=kml_routes,
            ghs_stats=ghs_stats,
            ghs_dir_stats=ghs_dir_stats,
            built_s_stats=built_s_stats,
            built_s_dir_stats=built_s_dir_stats,
            overture_stats=overture_stats,
            overture_dir_stats=overture_dir_stats,
            poi_stats=poi_stats,
            poi_dir_stats=poi_dir_stats,
        )

    write_errors_sheet(wb, city, bad)

    if excluded_stage2:
        write_excluded_sheet(wb, city, excluded_stage2)

    if unique_stops:
        write_unique_stops_sheet(wb, unique_stops, bbox)

    if stop_volumes and unique_stops:
        write_stop_volumes_sheet(wb, unique_stops, stop_volumes, vol_radius, bbox)

    if poi_stats and poi_values:
        write_poi_sheets(
            wb,
            city=city,
            ok=ok,
            poi_stats=poi_stats,
            poi_values=poi_values,
        )

    if dedup_removed:
        write_dedup_sheet(wb, dedup_removed)

    if generated_routes:
        write_generated_sheet(wb, generated_routes)

    if heatmap:
        write_heatmap_sheet(wb, heatmap)

    if include_stops:
        write_all_stops_sheet(wb, city=city, ok=ok, bbox=bbox)

    try:
        wb.save(path)
    except (PermissionError, OSError) as exc:
        logger.warning("Не удалось сохранить %s: %s", path, exc)
        return None

    return path


__all__ = ["build_xlsx"]