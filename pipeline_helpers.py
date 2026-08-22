"""Чистые вспомогательные функции pipeline.

Здесь нет сетевого I/O и нет orchestration; функции работают с уже загруженными
моделями и поэтому легко тестируются отдельно.
"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Any

from .config import CliConfig
from .enums import RouteType
from .models import RouteData, RouteTask
from .support import route_passes_number_filter


def route_type_set(config: CliConfig, all_types: set[RouteType]) -> set[RouteType]:
    """Возвращает выбранные пользователем типы маршрутов единым набором."""
    return set(config.type_filter) if config.type_filter else set(all_types)


def build_route_tasks(
    catalog: Any,
    city_slug: str,
    config: CliConfig,
    allowed_types: set[RouteType] | None = None,
) -> list[RouteTask]:
    """Строит задачи загрузки из всех выбранных секций каталога."""
    selected_types = allowed_types if allowed_types is not None else route_type_set(
        config, {section.route_type for section in catalog.sections}
    )
    route_filter = config.route_filter.strip().lower() if config.route_filter else None
    tasks: list[RouteTask] = []

    for section in catalog.sections:
        if section.route_type not in selected_types:
            continue
        if section.route_type in config.disabled_types:
            continue

        for link in section.links:
            if route_filter and not (
                link.name.strip().lower() == route_filter
                or str(link.route_id) == route_filter
            ):
                continue
            if not route_passes_number_filter(link.name, config.max_route_number):
                continue

            tasks.append(
                RouteTask(
                    city=city_slug,
                    route_type=section.route_type,
                    name=link.name,
                    route_id=link.route_id,
                )
            )

    return tasks


def apply_active_filter(
    routes: list[RouteData],
    active_only: bool,
) -> tuple[list[RouteData], int]:
    if not active_only:
        return list(routes), 0
    skipped = sum(1 for route in routes if not route.active)
    return [route for route in routes if route.active], skipped


def split_route_errors(
    routes: list[RouteData],
) -> tuple[list[RouteData], list[RouteData]]:
    ok = [route for route in routes if not route.error and route.directions]
    bad = [route for route in routes if route.error]
    return ok, bad


def apply_dedup_removals(
    routes: list[RouteData],
    meta: dict[Any, dict[str, Any]],
    removed_uids: set[Any],
) -> tuple[list[RouteData], int, int]:
    removed_dirs_by_route: dict[int, set[int]] = {}

    for uid in removed_uids:
        rec = meta.get(uid, {})
        route_id = rec.get("route_id")
        di = rec.get("di")
        if route_id is None or di is None:
            continue
        removed_dirs_by_route.setdefault(int(route_id), set()).add(int(di))

    new_routes: list[RouteData] = []
    fully_removed = 0
    shortened_routes = 0

    for route in routes:
        removed_dis = removed_dirs_by_route.get(route.route_id)
        if not removed_dis:
            new_routes.append(route)
            continue

        kept = tuple(
            direction
            for di, direction in enumerate(route.directions)
            if di not in removed_dis and len(direction.coords) >= 2
        )

        if not kept:
            fully_removed += 1
            continue
        if len(kept) < len(route.directions):
            shortened_routes += 1
        new_routes.append(replace(route, directions=kept))

    return new_routes, fully_removed, shortened_routes


def affected_route_ids(
    removed_uids: set[Any],
    meta: dict[Any, dict[str, Any]],
) -> set[int]:
    result: set[int] = set()
    for uid in removed_uids:
        route_id = meta.get(uid, {}).get("route_id")
        if route_id is not None:
            try:
                result.add(int(route_id))
            except (TypeError, ValueError):
                continue
    return result


def merge_recomputed_stats(
    stats: dict[int, Any],
    dir_stats: dict[tuple[int, int], Any],
    new_stats: dict[int, Any],
    new_dir_stats: dict[tuple[int, int], Any],
    affected_ids: set[int],
) -> None:
    for route_id in list(stats):
        if route_id in affected_ids and route_id not in new_stats:
            del stats[route_id]
    stats.update(new_stats)

    for key in list(dir_stats):
        if key[0] in affected_ids and key not in new_dir_stats:
            del dir_stats[key]
    dir_stats.update(new_dir_stats)


def route_inside_bbox(
    route: RouteData,
    bbox: tuple[float, float, float, float] | None,
) -> bool:
    if route.error or not route.directions or bbox is None:
        return False
    return all(
        direction.coords
        and all(
            math.isfinite(lat)
            and math.isfinite(lon)
            and bbox[0] <= lat <= bbox[2]
            and bbox[1] <= lon <= bbox[3]
            for lat, lon in direction.coords
        )
        for direction in route.directions
    )
