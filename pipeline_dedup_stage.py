"""Оркестрация одного прохода дедупликации pipeline."""

from __future__ import annotations

from typing import Any

from .dedup import dedup_network_after
from .dedup_policy import dedup_compute_removals
from .pipeline_dedup import apply_dedup_removals, dir_stat_volume_map
from .units import build_units, network_center_distances


def run_dedup_pass(
    routes: list[Any],
    analysis: dict[str, Any],
    config: Any,
    *,
    ghs_dir_stats: dict[tuple[int, int], Any] | None = None,
    active_set: set[int] | None = None,
    reporter: Any | None = None,
    pass_no: int = 1,
    max_passes: int = 1,
    network_before_km: float | None = None,
) -> dict[str, Any]:
    """Выполняет один логический проход dedup и возвращает его результат.

    Алгоритм выбора и удаления не меняется: orchestration только объединяет
    существующие операции в один явно тестируемый stage.
    """
    units = build_units(routes)
    if len(units) < 2:
        return {
            "removed": [],
            "active": active_set,
            "residual": {},
            "routes": routes,
            "fully_removed": 0,
            "shortened_routes": 0,
            "km_after": network_before_km,
            "unit_count": len(units),
        }

    ghs_volumes: dict[int, float] = {}
    if getattr(config, "ghs", False) and ghs_dir_stats:
        ghs_volumes = dir_stat_volume_map(
            analysis["ids"],
            analysis["meta"],
            ghs_dir_stats,
            "volume_m3",
        )

    center_distances = None
    if getattr(config, "dedup_center_weight", 0.0) > 0.0:
        center_distances = network_center_distances(units)

    removed, active, residual = dedup_compute_removals(
        analysis,
        config.dedup_threshold,
        ghs_volumes=ghs_volumes,
        ghs_weight=1.0,
        initial_active=active_set,
        center_distances=center_distances,
        center_weight=config.dedup_center_weight,
    )

    if not removed:
        return {
            "removed": [],
            "active": active,
            "residual": residual,
            "routes": routes,
            "fully_removed": 0,
            "shortened_routes": 0,
            "km_after": network_before_km,
            "unit_count": len(units),
        }

    removed_uids = {rec["маршрут"] for rec in removed}
    new_routes, fully_removed, shortened_routes = apply_dedup_removals(
        routes,
        analysis["meta"],
        removed_uids,
    )

    _, _, km_after = dedup_network_after(analysis, active)

    if reporter is not None:
        reporter.line(
            f"  Проход {pass_no}/{max_passes}: удалено направлений {len(removed)} "
            f"(маршрутов целиком: {fully_removed}, "
            f"сокращено маршрутов: {shortened_routes})"
        )

    return {
        "removed": removed,
        "active": active,
        "residual": residual,
        "routes": new_routes,
        "fully_removed": fully_removed,
        "shortened_routes": shortened_routes,
        "km_after": km_after,
        "unit_count": len(units),
    }


__all__ = ["run_dedup_pass"]
