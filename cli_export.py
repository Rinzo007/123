"""Экспортные операции CLI.

Содержит orchestration вокруг XLSX/KML/heatmap. Тяжёлая бизнес-логика
самих форматов остаётся в соответствующих модулях.
"""

from __future__ import annotations

import re
from typing import Any

from .errors import CatalogLoadError
from .heatmap import build_heatmap_kml
from .kml import build_kml
from .models import RouteData
from .report import render_errors
from .xlsx import build_xlsx


def output_base_name(output: str | None, city_slug: str) -> str:
    """Возвращает безопасное базовое имя выходных файлов."""
    safe_city_name = (
        re.sub(r"[^a-z0-9_-]+", "_", city_slug.lower()).strip("_") or "city"
    )
    base = output or f"{safe_city_name}_routes"
    return re.sub(r"\.(xlsx|kml)$", "", base, flags=re.IGNORECASE)


def write_xlsx(config: Any, result: Any, kml_routes: list[RouteData], output_base: str) -> None:
    limits = result.limits
    excluded_counts = result.excluded_counts
    xlsx_path = build_xlsx(
        result.all_routes,
        result.city_slug,
        result.city_title,
        output_base + ".xlsx",
        curv_limit=limits.curvilinearity,
        minlen_limit=limits.min_length_km,
        radius_limit=limits.radius_km,
        cut_curv=excluded_counts.get("криволинейность", 0),
        cut_len=excluded_counts.get("длина", 0),
        cut_radius=excluded_counts.get("радиус", 0),
        skipped_inactive=result.skipped_inactive,
        kml_routes=kml_routes,
        unique_stops=result.unique_stops,
        excluded_stage2=result.excluded_routes,
        heatmap=result.heatmap,
        include_stops=config.stops,
        bbox=result.bbox,
        ghs_stats=result.ghs_stats,
        ghs_meta=result.ghs_meta,
        ghs_dir_stats=result.ghs_dir_stats,
        poi_stats=result.poi_stats,
        poi_values=result.poi_values,
        poi_dir_stats=result.poi_dir_stats,
        poi_buffer_m=config.poi_buffer if config.poi else 0,
        dedup_removed=result.dedup_removed,
        dedup_analysis=result.dedup_analysis,
        net_metrics=result.net_metrics,
        generated_routes=result.generated_routes,
        built_s_stats=result.built_s_stats,
        built_s_meta=result.built_s_meta,
        built_s_dir_stats=result.built_s_dir_stats,
        overture_stats=result.overture_stats,
        overture_meta=result.overture_meta,
        overture_dir_stats=result.overture_dir_stats,
        stop_volumes=result.stop_volumes,
        vol_radius=config.heat_vol_radius,
    )
    if xlsx_path:
        print(f"  ✅ XLSX : {xlsx_path}")


def write_kml(config: Any, result: Any, kml_routes: list[RouteData], output_base: str) -> None:
    kml_path = build_kml(
        kml_routes,
        result.city_title,
        output_base + ".kml",
        include_stops=config.stops,
        bbox=result.bbox,
        ghs_stats=result.ghs_stats,
        ghs_buffer=config.ghs_buffer if config.ghs else 0,
        poi_stats=result.poi_stats,
        poi_buffer=config.poi_buffer if config.poi else 0,
        generated=result.generated_routes,
        built_s_stats=result.built_s_stats,
        built_s_meta=result.built_s_meta,
        overture_stats=result.overture_stats,
        overture_meta=result.overture_meta,
        overture_dir_stats=result.overture_dir_stats,
    )
    if kml_path:
        print(f"  ✅ KML  : {kml_path}  (маршрутов: {len(kml_routes)})")


def write_heatmap(config: Any, result: Any, output_base: str) -> None:
    if not result.heatmap:
        return
    path = build_heatmap_kml(
        result.heatmap,
        result.city_title,
        output_base + "_heatmap.kml",
        max_height=config.heat_max_height,
        flat=config.heat_flat,
    )
    if path:
        print(f"  ✅ KML heatmap: {path} (ячеек: {len(result.heatmap['cells'])})")


def export_outputs(config: Any, result: Any) -> None:
    print("\n[4/4] Генерация выводов...")
    kml_routes = result.ok_routes
    base = output_base_name(config.output, result.city_slug)
    if "xlsx" in config.output_formats:
        write_xlsx(config, result, kml_routes, base)
    if "kml" in config.output_formats:
        write_kml(config, result, kml_routes, base)
    write_heatmap(config, result, base)
    errors_text = render_errors(result.bad_routes)
    if errors_text:
        print(f"\n{errors_text}")


__all__ = ["output_base_name", "write_xlsx", "write_kml", "write_heatmap", "export_outputs"]
