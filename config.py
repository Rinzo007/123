"""Типизированная конфигурация запуска CLI.

``CliConfig`` — неизменяемая модель всех настроек, влияющих на расчёт.
Валидация значений и сборка модели из CLI-аргументов выполняются в
``build_cli_config`` (config.py), поэтому cli.py остаётся тонким адаптером.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from urllib.parse import unquote

from .catalog import city_from_url, make_catalog_url
from .constants import BASE_URL, CITY_ALIASES, DEFAULT_CITY
from .enums import RouteType

__all__ = [
    "CliConfig",
    "CliConfigError",
    "build_cli_config",
    "resolve_city_input",
]


class CliConfigError(ValueError):
    """Ошибка валидации аргументов CLI до начала расчёта."""


@dataclass(frozen=True, slots=True)
class CliConfig:
    """Все настройки, влияющие на расчёт конвейера."""

    city_input: str
    catalog_url: str
    type_filter: frozenset[RouteType]
    disabled_types: frozenset[RouteType]
    route_filter: str | None
    max_route_number: int
    no_bbox_filter: bool
    workers: int
    active_only: bool
    curv: float | None
    minlen: float | None
    radius: float | None
    center_lat: float | None
    center_lon: float | None
    bbox_buffer: float | None
    output_formats: frozenset[str]
    output: str | None
    stops: bool
    cache_dir: str
    ghs: bool
    ghs_file: str | None
    ghs_buffer: float
    ghs_s: bool
    ghs_s_file: str | None
    ghs_s_buffer: float | None
    ghs_s_max: float
    poi: bool
    poi_buffer: float
    dedup: bool
    dedup_buffer: float
    dedup_threshold: float
    dedup_passes: int
    dedup_center_weight: float
    ideas: bool
    ideas_search: str | None
    ideas_sort: str | None
    ideas_max_pages: int | None
    overture: bool
    overture_file: str | None
    overture_buffer: float
    overture_theme: str
    overture_release: str | None
    generate: bool
    gen_count: int
    gen_population: float | None
    gen_transfer: float
    gen_maxlen: float
    gen_corridor: float
    heatmap: bool
    heat_cell: float
    heat_alpha: str
    heat_gamma: float
    heat_top: float
    heat_max_height: float
    heat_flat: bool
    heat_volume: bool
    heat_vol_radius: float
    heat_smooth: bool


def resolve_city_input(
    raw_city: str | None,
    url_override: str | None,
) -> tuple[str, str]:
    """Преобразует аргументы города/URL в пару ``(city_input, catalog_url)``."""
    raw = str(raw_city or DEFAULT_CITY).strip()
    override = url_override

    if raw.lower().startswith(("http://", "https://")):
        if not override:
            override = raw

        raw = city_from_url(raw)
    elif "/" in raw and not override:
        override = f"{BASE_URL}/{raw.strip('/')}"
        raw = city_from_url(override)

    if override and not override.lower().startswith(("http://", "https://")):
        override = f"{BASE_URL}/{override.strip('/')}"

    raw = unquote(str(raw or DEFAULT_CITY)).strip().strip("/")
    raw = CITY_ALIASES.get(raw.lower(), raw)

    city_input = raw.lower().replace(" ", "-") or DEFAULT_CITY
    return city_input, make_catalog_url(city_input, override)


def _parse_type_flags(chunks: list[str] | None) -> set[RouteType]:
    """Разбирает ``--type`` / ``--no-type`` в набор типов."""
    result: set[RouteType] = set()

    if not chunks:
        return result

    for type_chunk in chunks:
        for type_part in type_chunk.split(","):
            type_part = type_part.strip().lower()

            if type_part:
                try:
                    result.add(RouteType(type_part))
                except ValueError as exc:
                    raise CliConfigError(f"Неизвестный тип: {type_part}.") from exc

    return result


def build_cli_config(args: argparse.Namespace) -> CliConfig:
    """Собирает типизированную конфигурацию из аргументов CLI.

    Бросает ``CliConfigError`` при некорректных значениях — вызывающий код
    решает, как сообщить об ошибке (sys.exit в CLI, исключение в тестах).
    """
    if args.workers <= 0:
        raise CliConfigError("--workers должен быть положительным числом")

    if args.dedup_passes <= 0:
        raise CliConfigError("--dedup-passes должен быть положительным числом")

    type_filter = _parse_type_flags(args.type)
    disabled_types = _parse_type_flags(args.no_type)

    if disabled_types & type_filter:
        raise CliConfigError(
            "Типы нельзя одновременно включать и исключать: "
            + ", ".join(sorted(t.value for t in disabled_types & type_filter))
            + "."
        )

    output_formats = {
        format_part.strip().lower()
        for format_part in args.format.split(",")
        if format_part.strip()
    }

    if not output_formats:
        raise CliConfigError("Не указан ни один формат вывода")

    if args.ghs and args.ghs_buffer <= 0:
        raise CliConfigError("--ghs-buffer должен быть положительным")

    if args.poi and args.poi_buffer <= 0:
        raise CliConfigError("--poi-buffer должен быть положительным")

    if args.ghs_s and args.ghs_s_buffer is not None and args.ghs_s_buffer <= 0:
        raise CliConfigError("--ghs-s-buffer должен быть положительным")

    if args.ghs_s and args.ghs_s_max <= 0:
        raise CliConfigError("--ghs-s-max должен быть положительным")

    if args.overture and args.overture_buffer <= 0:
        raise CliConfigError("--overture-buffer должен быть положительным")

    city_input, catalog_url = resolve_city_input(
        str(args.city_flag or args.city_arg or DEFAULT_CITY).strip(),
        args.url,
    )

    return CliConfig(
        city_input=city_input,
        catalog_url=catalog_url,
        type_filter=frozenset(type_filter),
        disabled_types=frozenset(disabled_types),
        route_filter=args.route,
        max_route_number=args.max_route_number,
        no_bbox_filter=args.no_bbox_filter,
        workers=args.workers,
        active_only=args.active_only,
        curv=args.curv,
        minlen=args.minlen,
        radius=args.radius,
        center_lat=args.center_lat,
        center_lon=args.center_lon,
        bbox_buffer=args.bbox_buffer,
        output_formats=frozenset(output_formats),
        output=args.output,
        stops=args.stops,
        cache_dir=args.cache_dir,
        ghs=args.ghs,
        ghs_file=args.ghs_file,
        ghs_buffer=args.ghs_buffer,
        ghs_s=args.ghs_s,
        ghs_s_file=args.ghs_s_file,
        ghs_s_buffer=args.ghs_s_buffer,
        ghs_s_max=args.ghs_s_max,
        poi=args.poi,
        poi_buffer=args.poi_buffer,
        dedup=args.dedup,
        dedup_buffer=args.dedup_buffer,
        dedup_threshold=args.dedup_threshold,
        dedup_passes=args.dedup_passes,
        dedup_center_weight=args.dedup_center_weight,
        ideas=args.ideas,
        ideas_search=args.ideas_search,
        ideas_sort=args.ideas_sort,
        ideas_max_pages=args.ideas_max_pages,
        overture=args.overture,
        overture_file=args.overture_file,
        overture_buffer=args.overture_buffer,
        overture_theme=args.overture_theme,
        overture_release=args.overture_release,
        generate=args.generate,
        gen_count=args.gen_count,
        gen_population=args.gen_population,
        gen_transfer=args.gen_transfer,
        gen_maxlen=args.gen_maxlen,
        gen_corridor=args.gen_corridor,
        heatmap=args.heatmap,
        heat_cell=args.heat_cell,
        heat_alpha=args.heat_alpha,
        heat_gamma=args.heat_gamma,
        heat_top=args.heat_top,
        heat_max_height=args.heat_max_height,
        heat_flat=args.heat_flat,
        heat_volume=args.heat_volume,
        heat_vol_radius=args.heat_vol_radius,
        heat_smooth=args.heat_smooth,
    )
