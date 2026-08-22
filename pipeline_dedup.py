"""Вспомогательные операции pipeline, связанные с дедупликацией."""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from .models import RouteData


def apply_dedup_removals(routes: list[RouteData], meta: dict[int, dict[str, Any]], removed_uids: set[int]) -> tuple[list[RouteData], int, int]:
    removed_dirs_by_route: dict[int, set[int]] = {}
    for uid in removed_uids:
        route_id = meta.get(uid, {}).get("route_id")
        di = meta.get(uid, {}).get("di")
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
        kept_directions = tuple(
            direction for di, direction in enumerate(route.directions)
            if di not in removed_dis and len(direction.coords) >= 2
        )
        if not kept_directions:
            fully_removed += 1
            continue
        if len(kept_directions) < len(route.directions):
            shortened_routes += 1
        new_routes.append(replace(route, directions=kept_directions))
    return new_routes, fully_removed, shortened_routes


def dir_stat_volume_map(unit_ids: list[int], meta: dict[int, dict[str, Any]], dir_stats: dict[tuple[int, int], Any], attr: str) -> dict[int, float]:
    result: dict[int, float] = {}
    for uid in unit_ids:
        rec = meta.get(uid, {})
        route_id = rec.get("route_id")
        di = rec.get("di")
        if route_id is None or di is None:
            continue
        stat = dir_stats.get((int(route_id), int(di)))
        if stat is not None:
            result[uid] = float(getattr(stat, attr))
    return result


def affected_route_ids(removed_uids: set[int], meta: dict[int, dict[str, Any]]) -> set[int]:
    affected: set[int] = set()
    for uid in removed_uids:
        route_id = meta.get(uid, {}).get("route_id")
        if route_id is None:
            continue
        try:
            affected.add(int(route_id))
        except (TypeError, ValueError):
            continue
    return affected


def merge_recomputed_stats(stats: dict[int, Any], dir_stats: dict[tuple[int, int], Any], new_stats: dict[int, Any], new_dir_stats: dict[tuple[int, int], Any], affected_ids: set[int]) -> None:
    for route_id in list(stats):
        if route_id in affected_ids and route_id not in new_stats:
            del stats[route_id]
    stats.update(new_stats)
    for key in list(dir_stats):
        if key[0] in affected_ids and key not in new_dir_stats:
            del dir_stats[key]
    dir_stats.update(new_dir_stats)

__all__ = ["apply_dedup_removals", "dir_stat_volume_map", "affected_route_ids", "merge_recomputed_stats"]
