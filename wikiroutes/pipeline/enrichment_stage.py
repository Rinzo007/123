"""Стадия обогащения: GHS-BUILT-V, GHS-BUILT-S и POI вдоль остановок."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..filters import compute_bbox
from ..errors import MissingDependencyError
from ..metrics import BuiltSStats, GhsStats, PoiStats
from ..models import RouteData
from ..overture.load import resolve_poi_place_file
from ..poi_stops import compute_poi_stops

if TYPE_CHECKING:
    from ..cache import JsonCache
    from ..config import CliConfig
    from ..report import Reporter


@dataclass(frozen=True, slots=True)
class EnrichmentResult:
    """Результат стадии обогащения."""

    ghs_stats: dict[int, GhsStats]
    ghs_meta: dict[str, Any] | None
    ghs_dir_stats: dict[tuple[int, int], GhsStats]
    built_s_stats: dict[int, BuiltSStats]
    built_s_meta: dict[str, Any] | None
    built_s_dir_stats: dict[tuple[int, int], BuiltSStats]
    poi_stops_stats: dict[int, PoiStats]
    poi_stops_meta: dict[str, Any] | None
    poi_stops_dir_stats: dict[tuple[int, int], PoiStats]


def _resolve_bbox(
    routes: list[RouteData],
    bbox: tuple[float, float, float, float] | None,
) -> tuple[float, float, float, float] | None:
    """Использует переданный bbox или вычисляет его из маршрутов."""
    if bbox is not None:
        return bbox
    bbox_model = compute_bbox(routes)
    if bbox_model is None:
        return None
    return (
        bbox_model.min_lat,
        bbox_model.min_lon,
        bbox_model.max_lat,
        bbox_model.max_lon,
    )


def _log_stats(line: Any, stats: dict[int, Any], total: str) -> None:
    """Отчёт о рассчитанных маршрутах (если они есть)."""
    if stats:
        line(f"  Рассчитано {len(stats)} маршрутов, суммарно {total}")


def _compute_ghs_block(
    routes: list[RouteData],
    config: CliConfig,
    city_slug: str,
    cache: JsonCache,
    line: Any,
) -> tuple[dict[int, GhsStats], dict[str, Any] | None, dict[tuple[int, int], GhsStats]]:
    """GHS-BUILT-V вдоль остановок."""
    stats: dict[int, GhsStats] = {}
    meta: dict[str, Any] | None = None
    dir_stats: dict[tuple[int, int], GhsStats] = {}
    if not config.ghs:
        return stats, meta, dir_stats

    try:
        from ..ghs.runtime import compute_ghs
    except ModuleNotFoundError as exc:
        if exc.name in {"wikiroutes.ghs", "wikiroutes.ghs.runtime"}:
            raise MissingDependencyError(
                "GHS включён, но модуль wikiroutes.ghs отсутствует в репозитории. "
                "Восстановите пакет GHS-BUILT-V/S или отключите --ghs/--ghs-s."
            ) from exc
        raise

    line(f"  GHS-BUILT-V вдоль остановок (буфер {config.ghs_buffer:.0f} м)...")
    stats, meta, dir_stats = compute_ghs(
        routes,
        config.ghs_file,
        config.ghs_buffer,
        city_slug,
        cache,
    )
    total = sum(stat.volume_m3 for stat in stats.values()) / 1e6
    _log_stats(line, stats, f"{total:.2f} млн м³")
    return stats, meta, dir_stats


def _compute_ghs_s_block(
    routes: list[RouteData],
    config: CliConfig,
    city_slug: str,
    cache: JsonCache,
    line: Any,
) -> tuple[dict[int, BuiltSStats], dict[str, Any] | None, dict[tuple[int, int], BuiltSStats]]:
    """GHS-BUILT-S вдоль маршрутов."""
    stats: dict[int, BuiltSStats] = {}
    meta: dict[str, Any] | None = None
    dir_stats: dict[tuple[int, int], BuiltSStats] = {}
    if not config.ghs_s:
        return stats, meta, dir_stats

    try:
        from ..ghs.runtime import compute_ghs_s
    except ModuleNotFoundError as exc:
        if exc.name in {"wikiroutes.ghs", "wikiroutes.ghs.runtime"}:
            raise MissingDependencyError(
                "GHS включён, но модуль wikiroutes.ghs отсутствует в репозитории. "
                "Восстановите пакет GHS-BUILT-V/S или отключите --ghs/--ghs-s."
            ) from exc
        raise

    ghs_s_buffer = (
        config.ghs_buffer if config.ghs_s_buffer is None else config.ghs_s_buffer
    )
    line(f"  GHS-BUILT-S вдоль маршрутов (буфер {ghs_s_buffer:.0f} м)...")
    stats, meta, dir_stats = compute_ghs_s(
        routes,
        config.ghs_s_file,
        ghs_s_buffer,
        city_slug,
        cache,
        max_val=config.ghs_s_max,
    )
    total = sum(stat.surface_m2 for stat in stats.values()) / 1e6
    _log_stats(line, stats, f"{total:.2f} млн м²")
    return stats, meta, dir_stats


def _resolve_poi_file(
    bbox: tuple[float, float, float, float] | None,
    poi_file_override: str | None,
    config: CliConfig,
    cache: JsonCache,
    line: Any,
) -> str | None:
    """Файл POI: переопределение → конфиг → автозагрузка Overture place."""
    return resolve_poi_place_file(
        poi_file_override,
        config.poi_stops_file,
        bbox,
        cache.root,
        config.overture_release,
        config.overture_download_retries,
        line,
    )


def _compute_poi_block(
    routes: list[RouteData],
    config: CliConfig,
    city_slug: str,
    cache: JsonCache,
    line: Any,
    bbox: tuple[float, float, float, float] | None,
    poi_file_override: str | None,
) -> tuple[dict[int, PoiStats], dict[str, Any] | None, dict[tuple[int, int], PoiStats]]:
    """POI вдоль остановок."""
    stats: dict[int, PoiStats] = {}
    meta: dict[str, Any] | None = None
    dir_stats: dict[tuple[int, int], PoiStats] = {}
    if not config.poi_stops:
        return stats, meta, dir_stats

    poi_stops_file = _resolve_poi_file(bbox, poi_file_override, config, cache, line)
    if poi_stops_file is None:
        return stats, meta, dir_stats

    line(f"  POI-stops вдоль остановок (буфер {config.poi_stops_buffer:.0f} м)...")
    stats, meta, dir_stats = compute_poi_stops(
        routes,
        poi_stops_file,
        config.poi_stops_buffer,
        city_slug,
        cache,
    )
    total = sum(stat.count for stat in stats.values())
    _log_stats(line, stats, f"{total} POI")
    return stats, meta, dir_stats


def run_enrichment_stage(
    routes: list[RouteData],
    config: CliConfig,
    *,
    city_slug: str,
    cache: JsonCache,
    bbox: tuple[float, float, float, float] | None = None,
    reporter: Reporter | None = None,
    poi_file_override: str | None = None,
) -> EnrichmentResult:
    """Считает GHS-BUILT-V, GHS-BUILT-S и POI вокруг остановок.

    Если `bbox` не передан, вычисляется из маршрутов.
    Если передан `poi_file_override`, он используется вместо `config.poi_stops_file`
    для POI-расчётов, что позволяет избежать повторной автозагрузки.
    """
    line = reporter.line if reporter is not None else lambda *_args: None
    bbox = _resolve_bbox(routes, bbox)

    ghs_stats, ghs_meta, ghs_dir_stats = _compute_ghs_block(
        routes, config, city_slug, cache, line
    )
    built_s_stats, built_s_meta, built_s_dir_stats = _compute_ghs_s_block(
        routes, config, city_slug, cache, line
    )
    poi_stops_stats, poi_stops_meta, poi_stops_dir_stats = _compute_poi_block(
        routes, config, city_slug, cache, line, bbox, poi_file_override
    )

    return EnrichmentResult(
        ghs_stats=ghs_stats,
        ghs_meta=ghs_meta,
        ghs_dir_stats=ghs_dir_stats,
        built_s_stats=built_s_stats,
        built_s_meta=built_s_meta,
        built_s_dir_stats=built_s_dir_stats,
        poi_stops_stats=poi_stops_stats,
        poi_stops_meta=poi_stops_meta,
        poi_stops_dir_stats=poi_stops_dir_stats,
    )


__all__ = ["EnrichmentResult", "run_enrichment_stage"]