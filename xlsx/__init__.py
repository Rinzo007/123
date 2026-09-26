"""Публичный фасад XLSX-экспорта.

Реализация находится в специализированных модулях.

Регистрация публичных функций:
- build_xlsx — создание отчета XLSX
- write_dedup_sheet — лист с результатами дедупликации
- write_errors_sheet — лист с ошибками
- write_unique_stops_sheet — лист уникальных остановок
- write_routes_sheet — лист с маршрутами
- write_summary_sheet — сводная таблица
- write_heatmap_sheet — тепловая карта
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import CliConfig, DEFAULT_DATA_DIR
from ..pipeline.runtime import PipelineResult
from .dedup import write_dedup_sheet
from .routes import write_routes_sheet
from .runtime import build_xlsx as _build_xlsx
from .simple import write_errors_sheet, write_unique_stops_sheet
from .summary import write_summary_sheet


def build_xlsx(
    config: CliConfig,
    result: PipelineResult,
    kml_routes: list[Any],
    output_path: str | Path,
    takt_riders: dict[str, float] | None = None,
) -> str | Path | None:
    """Создаёт XLSX-отчёт и возвращает путь к нему.

    Аргументы:
        config: объект конфигурации CLI
        result: результат работы пайплайна
        kml_routes: маршруты, попавшие в KML
        output_path: полный путь к файлу .xlsx
    """
    limits = result.limits
    excluded_counts = result.excluded_counts

    return _build_xlsx(
        routes=result.all_routes,
        city=result.city_slug,
        city_title=result.city_title,
        path=output_path,
        curv_limit=limits.curvilinearity,
        minlen_limit=limits.min_length_km,
        maxlen_limit=limits.max_length_km,
        radius_limit=limits.radius_km,
        cut_curv=excluded_counts.get("криволинейность", 0),
        cut_len=excluded_counts.get("длина", 0),
        cut_radius=excluded_counts.get("радиус", 0),
        removed_direction_routes=len(result.removed_directions),
        fully_excluded_routes=len(result.excluded_routes),
        skipped_inactive=result.skipped_inactive,
        kml_routes=kml_routes,
        unique_stops=result.unique_stops,
        excluded_stage2=result.excluded_routes,
        removed_directions=result.removed_directions,
        heatmap=result.heatmap,
        bbox=result.bbox,
        poi_stops_stats=result.poi_stops_stats,
        poi_stops_meta=result.poi_stops_meta,
        poi_stops_dir_stats=result.poi_stops_dir_stats,
        dedup_removed=result.dedup_removed,
        dedup_analysis=result.dedup_analysis,
        net_metrics=result.net_metrics,
        overture_stats=result.overture_stats,
        overture_meta=result.overture_meta,
        overture_dir_stats=result.overture_dir_stats,
        pack_data=config.pack_data or str(DEFAULT_DATA_DIR),
        pack_city=result.city_slug,
        takt_riders=takt_riders,
    )


__all__ = [
    "build_xlsx",
    "write_dedup_sheet",
    "write_errors_sheet",
    "write_routes_sheet",
    "write_summary_sheet",
    "write_unique_stops_sheet",
]