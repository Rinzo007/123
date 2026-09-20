"""Стадия фильтрации маршрутов."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

import requests
from shapely.geometry.base import BaseGeometry

from ..config import CliConfig
from ..errors import CityNotInUcdbError
from ..filters import apply_route_limits
from ..models import Direction, FilterLimits, RouteData
from ..osm.boundary import fetch_city_boundary
from ..report import Reporter

logger = logging.getLogger("wikiroutes.filter")


def resolve_city_boundary(
    config: CliConfig,
    sessions,
    cache,
    city_slug: str | None,
    reporter: Reporter,
) -> tuple[BaseGeometry | None, str | None]:
    """Определяет границу города до загрузки маршрутов.

    Обёртка над ``_fetch_boundary_geom``: возвращает ``(геометрия, источник)``
    или ``(None, None)`` при недоступной/выключенной границе. Площадь и
    предупреждения печатаются через ``reporter``. Может поднять
    ``CityNotInUcdbError``, если задан GHS-UCDB без растра и город не найден.
    """
    return _fetch_boundary_geom(
        config,
        sessions,
        cache,
        city_slug,
        reporter,
        route_coords=(),
    )


@dataclass(frozen=True, slots=True)
class FilterStageResult:
    """Типизированный результат стадии фильтрации."""

    ok_routes: list[RouteData]
    bad_routes: list[RouteData]
    skipped_inactive: int
    limits: FilterLimits
    excluded_routes: list[tuple[RouteData, str]]
    excluded_counts: dict[str, int]
    removed_directions: list[tuple[RouteData, Direction, str]]
    boundary_geom: BaseGeometry | None = None
    boundary_source: str | None = None


def build_filter_limits(config: CliConfig) -> FilterLimits:
    """Нормализует параметры фильтрации из CLI-конфигурации."""
    curvilinearity = config.curv if config.curv is not None else 0.0
    min_length = config.minlen if config.minlen is not None else 0.0
    max_length = config.maxlen if config.maxlen is not None else 0.0
    radius = config.radius if config.radius is not None else 0.0
    center_lat = config.center_lat
    center_lon = config.center_lon

    if radius > 0 and (center_lat is None or center_lon is None):
        radius = 0.0
        center_lat = None
        center_lon = None

    return FilterLimits(
        curvilinearity=curvilinearity,
        min_length_km=min_length,
        max_length_km=max_length,
        radius_km=radius,
        center_lat=center_lat,
        center_lon=center_lon,
    )


def split_active_routes(
    routes: Sequence[RouteData],
    *,
    active_only: bool,
) -> tuple[list[RouteData], int]:
    """Возвращает активные маршруты и число пропущенных неактивных."""
    if not active_only:
        return list(routes), 0

    active = [route for route in routes if route.active]
    return active, len(routes) - len(active)


def split_ok_and_errors(
    routes: Sequence[RouteData],
) -> tuple[list[RouteData], list[RouteData]]:
    """Разделяет маршруты на успешные и ошибочные."""
    ok = [route for route in routes if not route.error and route.directions]
    bad = [route for route in routes if route.error]
    return ok, bad


def apply_limits(
    routes: Sequence[RouteData],
    limits: FilterLimits,
    boundary_geom: BaseGeometry | None = None,
    boundary_buffer_m: float = 0.0,
) -> tuple[
    list[RouteData],
    list[tuple[RouteData, str]],
    dict[str, int],
    list[tuple[RouteData, Direction, str]],
]:
    """Применяет геометрические/числовые ограничения к направлениям маршрутов."""
    return apply_route_limits(
        routes,
        limits,
        boundary_geom=boundary_geom,
        boundary_buffer_m=boundary_buffer_m,
    )


def _fetch_ucdb_boundary(
    config: CliConfig,
    city_slug: str,
    route_coords,
    reporter: Reporter,
) -> tuple[BaseGeometry | None, str | None]:
    """Граница города из GHS-UCDB GeoPackage (наибольшая площадь)."""
    ucdb_paths = getattr(config, "ucdb", None)
    if not ucdb_paths:
        return None, None
    try:
        from ..ucdb_boundary import load_ucdb_boundary

        boundary_geom = load_ucdb_boundary(
            ucdb_paths,
            primary_name=city_slug,
            route_coords=route_coords,
        )
    except Exception as exc:  # noqa: BLE001 — UCDB недоступен → возврат к OSM
        reporter.line(
            f"  ⚠ Граница из GHS-UCDB недоступна, пробуем OSM: {exc}"
        )
        return None, None
    if boundary_geom is not None:
        return boundary_geom, "GHS-UCDB"
    paths_text = ", ".join(str(p) for p in ucdb_paths)
    logger.warning(
        "Город «%s» не найден в GHS-UCDB (%s) — пропускаем",
        city_slug,
        paths_text,
    )
    raise CityNotInUcdbError(
        f"Город «{city_slug}» не найден в GHS-UCDB ({paths_text}) "
        f"— экспорт пропущен."
    )


def _fetch_city_boundary_geom(
    config: CliConfig,
    city_slug: str,
    sessions,
    cache,
) -> BaseGeometry | None:
    """Геометрия города из OSM (Nominatim); None при недоступности OSM."""
    if sessions is None or cache is None:
        return None
    try:
        return fetch_city_boundary(
            city_slug,
            sessions.get(),
            cache,
            countrycodes=config.boundary_country,
            extra_relation_ids=config.boundary_extra,
        )
    except (requests.RequestException, ValueError, OSError):
        return None


def _fetch_osm_boundary(
    config: CliConfig,
    city_slug: str,
    sessions,
    cache,
    reporter: Reporter,
) -> tuple[BaseGeometry | None, str | None]:
    """Граница города из OSM (Nominatim) с отчётом об ошибках."""
    boundary_geom = _fetch_city_boundary_geom(
        config, city_slug, sessions, cache
    )
    if boundary_geom is None:
        reporter.line(
            "  ⚠ Граница города недоступна или не получена — "
            "фильтр по границе пропущен (маршруты сохранены)"
        )
        return None, None
    return boundary_geom, "OSM"


def _fetch_boundary_geom(
    config: CliConfig,
    sessions,
    cache,
    city_slug: str | None,
    reporter: Reporter,
    route_coords=(),
) -> tuple[BaseGeometry | None, str | None]:
    """Подтягивает границу города, если фильтр включён и доступен контекст.

    Возвращает ``(геометрия, источник)``, где источник — ``"GHS-BUILT-S"``,
    ``"GHS-UCDB"`` или ``"OSM"`` (None при недоступной границе). Если задан
    ``config.boundary_raster`` (растр GHS-BUILT-S), граница полностью строится
    из растра как «городская территория» вокруг центра города из OSM
    (центроид OSM-границы); UCDB при этом не читается вовсе. Если растр не
    дал результата, а OSM-граница получена — используется OSM-граница. При
    отсутствии ``boundary_raster`` поведение прежнее: ``config.ucdb`` (если
    задан — из GHS-UCDB GeoPackage, наибольшая площадь; иначе — OSM).
    """
    if not (config.boundary and city_slug):
        return None, None
    if getattr(config, "boundary_raster", None):
        return _fetch_urban_boundary(
            config, sessions, cache, city_slug, reporter
        )
    result = _fetch_ucdb_boundary(config, city_slug, route_coords, reporter)
    if result != (None, None):
        return result
    return _fetch_osm_boundary(config, city_slug, sessions, cache, reporter)


def _fetch_urban_boundary(
    config: CliConfig,
    sessions,
    cache,
    city_slug: str,
    reporter: Reporter,
) -> tuple[BaseGeometry | None, str | None]:
    """Строит границу города из растра GHS-BUILT-S вокруг OSM-центра.

    Центр берётся как центроид OSM-границы города (``fetch_city_boundary``),
    затем ``build_urban_boundary`` выделяет «городскую территорию». Возвращает
    ``(полигон в WGS84, источник)``: ``"GHS-BUILT-S"`` при успехе растра,
    ``"OSM"`` — если растр не дал результата, но OSM-граница получена, и
    ``(None, None)`` при недоступности данных/растра/OSM.
    """
    raster = getattr(config, "boundary_raster", None)
    if not raster or sessions is None or cache is None:
        return None, None
    osm_geom = _fetch_city_boundary_geom(config, city_slug, sessions, cache)
    if osm_geom is None or osm_geom.is_empty:
        reporter.line(
            "  ⚠ OSM-граница для растра не получена — фильтр по границе пропущен"
        )
        return None, None
    try:
        from ..urban_boundary import build_urban_boundary
    except Exception as exc:  # noqa: BLE001 — растр недоступен → OSM-граница
        reporter.line(
            f"  ⚠ Растр GHS-BUILT-S недоступен, используем OSM-границу: {exc}"
        )
        return osm_geom, "OSM"
    centroid = osm_geom.centroid
    urban = build_urban_boundary(centroid.x, centroid.y, raster)
    if urban is None or urban.is_empty:
        reporter.line(
            "  ⚠ Не удалось построить границу из растра GHS-BUILT-S — "
            "используем OSM-границу"
        )
        return osm_geom, "OSM"
    return urban, "GHS-BUILT-S"


def report_boundary_area(
    boundary_geom: BaseGeometry,
    source: str,
    reporter: Reporter,
) -> None:
    """Пишет геометрию, источник и площадь границы.

    Площадь считается геодезически (UTM-зона по центроиду); при сбое
    трансформации — в квадратных градусах.
    """
    label = f"Граница из {source}"
    try:
        from pyproj import Transformer
        from shapely.ops import transform as shapely_transform

        centroid = boundary_geom.centroid
        utm_zone = int((centroid.x + 180) / 6) + 1
        epsg = 32600 + utm_zone
        to_utm = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
        area_km2 = shapely_transform(to_utm.transform, boundary_geom).area / 1e6
        reporter.line(
            f"  {label}: {boundary_geom.geom_type}, "
            f"площадь ~{area_km2:.2f} км²"
        )
    except Exception:  # noqa: BLE001 — fallback площади в градусах при сбое трансформации
        reporter.line(
            f"  {label}: {boundary_geom.geom_type}, "
            f"площадь ~{boundary_geom.area:.4f} кв.°"
        )


def _apply_limits_with_net(
    routes: Sequence[RouteData],
    limits: FilterLimits,
    boundary_geom: BaseGeometry | None,
    boundary_buffer_m: float,
    reporter: Reporter,
) -> tuple[
    list[RouteData],
    list[tuple[RouteData, str]],
    dict[str, int],
    list[tuple[RouteData, Direction, str]],
]:
    """Применяет ограничения; отменяет граничный фильтр, если он отсекает всё."""
    result = apply_limits(
        routes,
        limits,
        boundary_geom=boundary_geom,
        boundary_buffer_m=boundary_buffer_m,
    )
    ok_routes = result[0]
    # Страховка: непригодная граница (фрагмент/не по адресу) может отсечь ВСЕ
    # маршруты. В таком случае фильтр по границе бесполезен — отменяем его и
    # сохраняем маршруты, иначе экспорт оказался бы пустым.
    if boundary_geom is not None and len(routes) > 0 and len(ok_routes) == 0:
        reporter.line(
            "  ⚠ Граница города отсекла все маршруты — считаем её непригодной "
            "и пропускаем фильтр по границе (маршруты сохранены)"
        )
        return apply_limits(
            routes,
            limits,
            boundary_geom=None,
            boundary_buffer_m=0.0,
        )
    return result


def _resolve_boundary(
    config: CliConfig,
    precomputed_boundary: tuple[BaseGeometry | None, str | None] | None,
    ok_routes: list[RouteData],
    sessions,
    cache,
    city_slug: str | None,
    reporter: Reporter,
) -> tuple[BaseGeometry | None, str | None]:
    """Граница города: из precomputed либо запрос по маршрутам (UCDB/OSM/растр)."""
    if precomputed_boundary is not None:
        return precomputed_boundary
    boundary_geom, boundary_source = _fetch_boundary_geom(
        config,
        sessions,
        cache,
        city_slug,
        reporter,
        route_coords=(
            coord
            for route in ok_routes
            for direction in route.directions
            for coord in direction.coords
        ),
    )
    if boundary_geom is not None and boundary_source is not None:
        report_boundary_area(boundary_geom, boundary_source, reporter)
    return boundary_geom, boundary_source


def run_filter_stage(
    routes: Sequence[RouteData],
    config: CliConfig,
    *,
    reporter: Reporter,
    sessions=None,
    cache=None,
    city_slug: str | None = None,
    precomputed_boundary: tuple[BaseGeometry | None, str | None] | None = None,
) -> FilterStageResult:
    """Выполняет active-only, разделение ошибок и route limits.

    Если включён фильтр по границе города (``config.boundary``) и доступны
    сессия/кэш/название города, граница подтягивается из OSM (Nominatim) и
    направления вне неё отсеиваются. При недоступности границы фильтр
    молча пропускается (предупреждение в отчёте), расчёт не прерывается.

    ``precomputed_boundary`` — кортеж ``(геометрия, источник)``, уже
    определённый до загрузки маршрутов; если передан, повторного запроса
    границы не выполняется (площадь и предупреждения не дублируются).
    """
    working = routes
    skipped_inactive = 0

    if config.active_only:
        working, skipped_inactive = split_active_routes(working, active_only=True)

    ok_routes, bad_routes = split_ok_and_errors(working)
    reporter.line(f"  Успешно: {len(ok_routes)} | ошибок: {len(bad_routes)}")

    limits = build_filter_limits(config)
    configured_radius = config.radius or 0.0
    if configured_radius > 0 and limits.radius_km == 0.0:
        reporter.line(
            "  ⚠ Фильтр по радиусу игнорируется: укажите --center-lat и --center-lon"
        )

    boundary_geom, boundary_source = _resolve_boundary(
        config,
        precomputed_boundary,
        ok_routes,
        sessions,
        cache,
        city_slug,
        reporter,
    )
    boundary_buffer_m = config.boundary_buffer if config.boundary else 0.0

    ok_routes, excluded_routes, direction_counts, removed_directions = (
        _apply_limits_with_net(
            ok_routes,
            limits,
            boundary_geom,
            boundary_buffer_m,
            reporter,
        )
    )

    if boundary_geom is not None and len(ok_routes) > 0:
        reporter.line(
            f"  Маршрутов после фильтра по границе города: {len(ok_routes)}"
        )
    if direction_counts["граница"]:
        reporter.line(
            f"  Удалено направлений вне границы города: {direction_counts['граница']}"
        )

    return FilterStageResult(
        ok_routes=ok_routes,
        bad_routes=bad_routes,
        skipped_inactive=skipped_inactive,
        limits=limits,
        excluded_routes=excluded_routes,
        excluded_counts=direction_counts,
        removed_directions=removed_directions,
        boundary_geom=boundary_geom,
        boundary_source=boundary_source,
    )


__all__ = [
    "FilterStageResult",
    "apply_limits",
    "build_filter_limits",
    "report_boundary_area",
    "resolve_city_boundary",
    "run_filter_stage",
    "split_active_routes",
    "split_ok_and_errors",
]
