"""Публичный фасад pipeline.

Реализация вынесена в ``pipeline_runtime``. Функциональные stage-helper'ы
подключаются из специализированных модулей, сохраняя прежние импорты.
"""

from . import pipeline_runtime as _runtime
from .pipeline_runtime import *  # noqa: F401,F403
from .pipeline_bbox import compute_pipeline_bbox, route_inside_bbox, select_secondary_routes
from .pipeline_dedup import (
    affected_route_ids,
    apply_dedup_removals,
    dir_stat_volume_map,
    merge_recomputed_stats,
)
from .pipeline_loading import build_route_tasks

# Совместимость с текущим runtime: единая стадия загрузки используется вместо
# двух групп маршрутов. Второй вызов намеренно пустой, чтобы не загружать сеть дважды.
_runtime.build_base_tasks = build_route_tasks
_runtime.build_secondary_tasks = lambda *args, **kwargs: []
_runtime.compute_pipeline_bbox = compute_pipeline_bbox
_runtime.select_secondary_routes = select_secondary_routes
_runtime._route_inside_bbox = route_inside_bbox
_runtime._apply_dedup_removals = apply_dedup_removals
_runtime._dir_stat_volume_map = dir_stat_volume_map
_runtime._dedup_affected_route_ids = affected_route_ids
_runtime._merge_recomputed_stats = merge_recomputed_stats

__all__ = [name for name in dir(_runtime) if not name.startswith("_")] + [
    "build_route_tasks",
    "compute_pipeline_bbox",
    "route_inside_bbox",
    "select_secondary_routes",
    "affected_route_ids",
    "apply_dedup_removals",
    "dir_stat_volume_map",
    "merge_recomputed_stats",
]
