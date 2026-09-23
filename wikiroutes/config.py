"""Конфигурация CLI и runtime приложения WikiRoutes.

Единая точка сборки настроек между argparse и pipeline. Значения по умолчанию
совместимы с текущим Takt/OD/passenger-flow runtime.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .catalog import make_catalog_url
from .constants import (
    BASE_URL,
    DEFAULT_CITY,
    MAX_WORKERS,
    ROUTE_CATALOG_BINDINGS,
)
from .enums import ExportFormat, RouteType, parse_route_type


DEFAULT_DATA_DIR = Path(
    os.getenv("WIKIROUTES_DATA_DIR", r"D:\\Programs\\Cities2")
)
DEFAULT_GHS_DIR = Path(
    os.getenv("WIKIROUTES_GHS_DIR", str(DEFAULT_DATA_DIR / "GHS"))
)


def _discover_ghs_population_file() -> str | None:
    """Находит локальный raster населения в каталоге GHS.

    Явный ``WIKIROUTES_GHS_FILE`` имеет приоритет. Без него берётся первый
    TIFF с ``pop``/``population`` в имени.
    """
    explicit = os.getenv("WIKIROUTES_GHS_FILE")
    if explicit:
        return explicit

    if not DEFAULT_GHS_DIR.is_dir():
        return None

    candidates = sorted(
        (
            *DEFAULT_GHS_DIR.glob("*pop*.tif"),
            *DEFAULT_GHS_DIR.glob("*pop*.tiff"),
            *DEFAULT_GHS_DIR.glob("*population*.tif"),
            *DEFAULT_GHS_DIR.glob("*population*.tiff"),
        ),
        key=lambda path: path.name.lower(),
    )
    seen: set[str] = set()
    for path in candidates:
        key = str(path.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        return str(path)
    return None

DEFAULT_GHS_FILE = _discover_ghs_population_file()
DEFAULT_UCDB_PATHS: tuple[str, ...] = (
    os.getenv(
        "WIKIROUTES_UCDB_2015",
        str(DEFAULT_GHS_DIR / "GHS_STAT_UCDB2015MT_GLOBE_R2019A_V1_2.gpkg"),
    ),
    os.getenv(
        "WIKIROUTES_UCDB_2024",
        str(DEFAULT_GHS_DIR / "GHS_UCDB_GLOBE_R2024A.gpkg"),
    ),
)
DEFAULT_CACHE_DIR = os.getenv(
    "WIKIROUTES_CACHE_DIR",
    str(DEFAULT_DATA_DIR / "wikiroutes_cache"),
)


class CliConfigError(ValueError):
    """Некорректная комбинация параметров CLI."""


def _split_values(values: Any) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        return [item.strip() for item in values.split(",") if item.strip()]
    result: list[str] = []
    for value in values:
        result.extend(item.strip() for item in str(value).split(",") if item.strip())
    return result


def _route_types(values: Any) -> frozenset[RouteType]:
    try:
        return frozenset(parse_route_type(value) for value in _split_values(values))
    except ValueError as exc:
        raise CliConfigError(f"Неизвестный тип маршрута: {exc}") from exc


def _output_formats(value: str | None) -> frozenset[str]:
    values = {item.strip().lower() for item in _split_values(value)}
    allowed = {item.value for item in ExportFormat}
    unknown = values - allowed
    if unknown:
        raise CliConfigError(
            "Неизвестный формат вывода: " + ", ".join(sorted(unknown))
        )
    return frozenset(values or {ExportFormat.XLSX.value, ExportFormat.KML.value})


def _map_type(value: Any) -> RouteType | None:
    if not value:
        return None
    try:
        return parse_route_type(value)
    except ValueError as exc:
        raise CliConfigError(f"Неизвестный тип для --map-type: {value}") from exc


def _extra_relations(value: Any) -> tuple[int, ...]:
    if not value:
        return ()
    result: list[int] = []
    for item in _split_values(value):
        try:
            result.append(int(item))
        except ValueError as exc:
            raise CliConfigError(f"Некорректный --boundary-extra: {item}") from exc
    return tuple(result)


def _ucdb_paths(value: Any) -> tuple[str, ...] | None:
    if value is None:
        return None
    values = tuple(_split_values(value))
    return values or DEFAULT_UCDB_PATHS


@dataclass
class CliConfig:
    # Application / output
    city_input: str = DEFAULT_CITY
    catalog_url: str = f"{BASE_URL}/{DEFAULT_CITY}/catalog"
    route_catalog_city: str = ""
    output_formats: frozenset[str] = field(
        default_factory=lambda: frozenset({"xlsx", "kml"})
    )
    output: str | None = None
    workers: int = MAX_WORKERS
    stops: bool = False
    terminals_geojson: bool = False
    boundary_geojson: bool = False
    map_type: RouteType | None = None

    # Route filters
    type_filter: frozenset[RouteType] = field(default_factory=frozenset)
    disabled_types: frozenset[RouteType] = field(default_factory=frozenset)
    route_filter: str | None = None
    max_route_number: int = 0
    curv: float = 0.0
    minlen: float | None = None
    maxlen: float | None = None
    radius: float | None = None
    center_lat: float | None = None
    center_lon: float | None = None
    active_only: bool = False
    boundary: bool = True
    boundary_buffer: float = 500.0
    boundary_country: str = "ru"
    boundary_extra: tuple[int, ...] = ()
    boundary_raster: str | None = None
    ucdb: tuple[str, ...] | None = None

    # Enrichment / spatial
    ghs: bool = False
    ghs_file: str | None = None
    ghs_buffer: float = 600.0
    ghs_s: bool = False
    ghs_s_file: str | None = None
    ghs_s_buffer: float | None = None
    ghs_s_max: float = 1e12
    overture: bool = False
    overture_file: str | None = None
    overture_buffer: float = 500.0
    overture_theme: str = "place"
    overture_release: str | None = None
    overture_download_retries: int = 3
    poi: bool = False
    poi_buffer: float | None = None
    poi_stops: bool = False
    poi_stops_file: str | None = None
    poi_stops_buffer: float = 500.0

    # Dedup
    dedup: bool = False
    dedup_metric: str | None = None
    dedup_buffer: float = 500.0
    dedup_threshold: float = 0.70
    dedup_passes: int = 1
    dedup_unique_km: float = 0.0
    dedup_cache: bool = True
    dedup_cache_dir: str | None = None
    dedup_approx: bool = False
    dedup_approx_step: float | None = None
    dedup_approx_margin: float = 0.0
    dedup_profile: str = "exact"
    dedup_unique_net: bool = True

    # Heatmap / ideas / OSM
    heatmap: bool = False
    heat_cell: float = 0.1
    heat_alpha: str = "ff"
    heat_gamma: float = 0.6
    heat_top: float = 35.0
    heat_max_height: float = 400.0
    heat_flat: bool = False
    heat_smooth: bool = False
    ideas: bool = False
    ideas_max_pages: int | None = None
    ideas_search: str | None = None
    ideas_sort: str | None = None
    osm_routes: bool = False

    # OD
    od_matrix: bool = False
    od_zone_size_m: float = 500.0
    od_decay_minutes: float = 12.0
    od_zones_file: str | None = None
    od_matrix_file: str | None = None
    od_demand_file: str | None = None
    od_purposes: bool = False
    od_purposes_file: str | None = None
    od_weights_file: str | None = None
    od_districts_file: str | None = None

    # Passenger flow / Takt defaults
    passenger_flow: bool = False
    flow_car_mode: bool = True
    flow_no_car_share: float = 0.35
    flow_car_parking_min: float = 4.0
    flow_car_cost_per_km_eur: float = 0.25
    flow_car_parking_eur: float = 1.5
    flow_car_circuity: float = 1.3
    flow_car_speed_kmh: float = 25.0
    flow_walk_speed_mps: float = 1.33
    flow_walk_circuity: float = 1.25
    flow_vot_per_eur_s: float = 360.0
    flow_fare_base_eur: float = 0.6
    flow_fare_per_km_eur: float = 0.12
    flow_fare_cap_eur: float = 3.0
    flow_fare_min_eur: float = 0.0
    flow_two_wheel_share: float = 0.30
    flow_two_wheel_speed_mps: float = 4.2
    flow_two_wheel_reach_m: float = 7000.0
    flow_two_wheel_per_km_eur: float = 0.03
    flow_periods: Any = None
    flow_stop_time_min: float = 2.0
    flow_logit_temp: float = 10.0
    flow_stop_search_radius_m: float = 1500.0
    flow_wait_time_min: float = 0.0
    flow_walk_to_stop_min: float = 0.0
    flow_transfer_penalty_min: float = 10.0
    flow_transfer_penalty_calc: str = "takt"
    flow_transfer_radius_m: float = 800.0
    flow_max_transfers: int = 3
    flow_headway_min: float | None = 10.0
    flow_wait_crowding_per_100_min: float = 0.1
    flow_transfer_wait_min: float | None = None
    flow_capex_factor: float = 1.0
    flow_wait_calc: str = "takt"
    flow_include_reliability: bool = True
    flow_msa_max_iterations: int = 20
    flow_msa_gap: float = 0.01

    # Runtime flags
    no_cache: bool = False
    refresh: bool = False
    cache_dir: str = DEFAULT_CACHE_DIR


def build_cli_config(args: Any) -> CliConfig:
    """Собирает типизированную конфигурацию из argparse Namespace."""
    city = str(getattr(args, "city_flag", None) or getattr(args, "city_arg", None) or DEFAULT_CITY).strip()
    city_url = make_catalog_url(city, getattr(args, "url", None))
    catalog_city = city.strip("/").split("/")[0].lower() if "/" in city else city.lower()

    env_binding = os.getenv("WIKIROUTES_ROUTE_CATALOG", "").strip()
    route_catalog_city = env_binding or ROUTE_CATALOG_BINDINGS.get(catalog_city, "")

    boundary_raster = getattr(args, "boundary_raster", None)
    config = CliConfig(
        city_input=catalog_city,
        catalog_url=city_url,
        route_catalog_city=route_catalog_city,
        output_formats=_output_formats(getattr(args, "format", None)),
        output=getattr(args, "output", None),
        workers=max(1, int(getattr(args, "workers", MAX_WORKERS))),
        stops=bool(getattr(args, "stops", False)),
        terminals_geojson=bool(getattr(args, "terminals_geojson", False)),
        boundary_geojson=bool(getattr(args, "boundary_geojson", False)),
        map_type=_map_type(getattr(args, "map_type", None)),
        type_filter=_route_types(getattr(args, "type", None)),
        disabled_types=_route_types(getattr(args, "no_type", None)),
        route_filter=getattr(args, "route", None),
        max_route_number=max(0, int(getattr(args, "max_route_number", 0))),
        curv=float(getattr(args, "curv", 0.0) or 0.0),
        minlen=getattr(args, "minlen", None),
        maxlen=getattr(args, "maxlen", None),
        radius=getattr(args, "radius", None),
        center_lat=getattr(args, "center_lat", None),
        center_lon=getattr(args, "center_lon", None),
        active_only=bool(getattr(args, "active_only", False)),
        boundary=bool(getattr(args, "boundary", True)),
        boundary_buffer=float(getattr(args, "boundary_buffer", 500.0) or 0.0),
        boundary_country=getattr(args, "boundary_country", "ru") or "ru",
        boundary_extra=_extra_relations(getattr(args, "boundary_extra", "")),
        boundary_raster=boundary_raster,
        ucdb=_ucdb_paths(getattr(args, "ucdb", None)),
        ghs=bool(getattr(args, "ghs", False)),
        ghs_file=getattr(args, "ghs_file", None) or DEFAULT_GHS_FILE,
        ghs_buffer=float(getattr(args, "ghs_buffer", 600.0) or 0.0),
        ghs_s=bool(getattr(args, "ghs_s", False)),
        ghs_s_file=getattr(args, "ghs_s_file", None),
        ghs_s_buffer=getattr(args, "ghs_s_buffer", None),
        ghs_s_max=float(getattr(args, "ghs_s_max", 1e12) or 0.0),
        overture=bool(getattr(args, "overture", False)),
        overture_file=getattr(args, "overture_file", None),
        overture_buffer=float(getattr(args, "overture_buffer", 500.0) or 0.0),
        overture_theme=getattr(args, "overture_theme", "place") or "place",
        overture_release=getattr(args, "overture_release", None),
        overture_download_retries=max(0, int(getattr(args, "overture_download_retries", 3))),
        poi=bool(getattr(args, "poi", False)),
        poi_buffer=getattr(args, "poi_buffer", None),
        poi_stops=bool(getattr(args, "poi_stops", False)),
        poi_stops_file=getattr(args, "poi_stops_file", None),
        poi_stops_buffer=float(getattr(args, "poi_stops_buffer", 500.0) or 0.0),
        dedup=bool(getattr(args, "dedup", False)),
        dedup_buffer=float(getattr(args, "dedup_buffer", 500.0) or 0.0),
        dedup_threshold=float(getattr(args, "dedup_threshold", 0.70) or 0.0),
        dedup_passes=max(1, int(getattr(args, "dedup_passes", 1))),
        dedup_unique_km=max(0.0, float(getattr(args, "dedup_unique_km", 0.0) or 0.0)),
        dedup_cache=bool(getattr(args, "dedup_cache", True)),
        dedup_cache_dir=getattr(args, "dedup_cache_dir", None),
        dedup_approx=bool(getattr(args, "dedup_approx", False)),
        dedup_approx_step=getattr(args, "dedup_approx_step", None),
        dedup_approx_margin=max(0.0, float(getattr(args, "dedup_approx_margin", 0.0) or 0.0)),
        dedup_profile=getattr(args, "dedup_profile", "exact") or "exact",
        dedup_unique_net=bool(getattr(args, "dedup_unique_net", True)),
        heatmap=bool(getattr(args, "heatmap", False)),
        heat_cell=float(getattr(args, "heat_cell", 0.1) or 0.0),
        heat_alpha=getattr(args, "heat_alpha", "ff") or "ff",
        heat_gamma=float(getattr(args, "heat_gamma", 0.6) or 0.0),
        heat_top=float(getattr(args, "heat_top", 35.0) or 0.0),
        heat_max_height=float(getattr(args, "heat_max_height", 400.0) or 0.0),
        heat_flat=bool(getattr(args, "heat_flat", False)),
        heat_smooth=bool(getattr(args, "heat_smooth", False)),
        ideas=bool(getattr(args, "ideas", False)),
        ideas_max_pages=getattr(args, "ideas_max_pages", None),
        ideas_search=getattr(args, "ideas_search", None),
        ideas_sort=getattr(args, "ideas_sort", None),
        osm_routes=bool(getattr(args, "osm_routes", False)),
        passenger_flow=bool(getattr(args, "passenger_flow", False)),
        no_cache=bool(getattr(args, "no_cache", False)),
        refresh=bool(getattr(args, "refresh", False)),
        cache_dir=str(getattr(args, "cache_dir", DEFAULT_CACHE_DIR) or DEFAULT_CACHE_DIR),
    )

    # Полный пассажиропоток всегда требует OD. Разложение по целям включаем
    # тем же флагом, чтобы periods/Takt-профили доходили до passenger_flow.
    if config.passenger_flow:
        config.od_matrix = True
        config.od_purposes = True

    if config.no_cache and config.refresh:
        raise CliConfigError("--no-cache и --refresh нельзя использовать вместе")
    if config.map_type is not None and config.map_type not in set(RouteType):
        raise CliConfigError("Некорректный --map-type")
    if config.dedup_profile not in {"exact", "fast"}:
        raise CliConfigError("--dedup-profile должен быть exact или fast")
    if config.flow_wait_calc not in {"takt", "linear"}:
        raise CliConfigError("flow_wait_calc должен быть takt или linear")
    if config.flow_transfer_penalty_calc not in {"takt", "fixed"}:
        raise CliConfigError("flow_transfer_penalty_calc должен быть takt или fixed")
    return config


__all__ = [
    "CliConfig",
    "CliConfigError",
    "DEFAULT_CACHE_DIR",
    "DEFAULT_DATA_DIR",
    "DEFAULT_GHS_DIR",
    "DEFAULT_GHS_FILE",
    "DEFAULT_UCDB_PATHS",
    "build_cli_config",
]
