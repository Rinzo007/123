"""Построение задач загрузки маршрутов."""
from __future__ import annotations

from typing import Any

from .config import CliConfig
from .enums import RouteType
from .models import RouteTask
from .support import route_passes_number_filter

BASE_ROUTE_TYPES = frozenset({
    RouteType.TRAM,
    RouteType.TROLLEYBUS,
    RouteType.METRO,
    RouteType.ELECTROBUS,
})


def base_types(config: CliConfig) -> set[RouteType]:
    return set(BASE_ROUTE_TYPES) if not config.type_filter else set(config.type_filter) & set(BASE_ROUTE_TYPES)


def secondary_types(config: CliConfig) -> set[RouteType]:
    all_types = set(RouteType)
    return all_types - set(BASE_ROUTE_TYPES) if not config.type_filter else set(config.type_filter) - set(BASE_ROUTE_TYPES)


def _build_tasks(catalog: Any, city_slug: str, config: CliConfig, allowed: set[RouteType]) -> list[RouteTask]:
    route_filter = config.route_filter.strip().lower() if config.route_filter else None
    tasks: list[RouteTask] = []
    for section in catalog.sections:
        if section.route_type not in allowed or section.route_type in config.disabled_types:
            continue
        for link in section.links:
            if route_filter and not (link.name.strip().lower() == route_filter or str(link.route_id) == route_filter):
                continue
            if not route_passes_number_filter(link.name, config.max_route_number):
                continue
            tasks.append(RouteTask(city=city_slug, route_type=section.route_type, name=link.name, route_id=link.route_id))
    return tasks


def build_base_tasks(catalog: Any, city_slug: str, config: CliConfig) -> list[RouteTask]:
    return _build_tasks(catalog, city_slug, config, base_types(config))


def build_secondary_tasks(catalog: Any, city_slug: str, config: CliConfig) -> list[RouteTask]:
    return _build_tasks(catalog, city_slug, config, secondary_types(config))

__all__ = ["BASE_ROUTE_TYPES", "base_types", "secondary_types", "build_base_tasks", "build_secondary_tasks"]
