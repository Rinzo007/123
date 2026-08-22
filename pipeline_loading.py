"""Единая стадия построения задач загрузки маршрутов."""

from __future__ import annotations

from typing import Any

from .config import CliConfig
from .models import RouteTask
from .support import route_passes_number_filter


def build_route_tasks(catalog: Any, city_slug: str, config: CliConfig) -> list[RouteTask]:
    """Строит задачи загрузки для всех разрешённых типов маршрутов."""
    route_filter = config.route_filter.strip().lower() if config.route_filter else None
    tasks: list[RouteTask] = []
    for section in catalog.sections:
        if section.route_type in config.disabled_types:
            continue
        for link in section.links:
            if route_filter and not (link.name.strip().lower() == route_filter or str(link.route_id) == route_filter):
                continue
            if not route_passes_number_filter(link.name, config.max_route_number):
                continue
            tasks.append(RouteTask(city=city_slug, route_type=section.route_type, name=link.name, route_id=link.route_id))
    return tasks

__all__ = ["build_route_tasks"]
