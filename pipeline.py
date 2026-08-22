"""Публичный фасад pipeline.

Реализация вынесена в ``pipeline_runtime``. Functional stage-helper'ы
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
from .pipeline_dedup_stage import run_dedup_pass
from .pipeline_enrichment import compute_ghs, compute_ghs_s, compute_overture, compute_poi
from .pipeline_filtering import (
    apply_limits,
    build_filter_limits,
    split_active_routes,
    split_ok_and_errors,
)
from .pipeline_loading import build_route_tasks
from .pipeline_types import PipelineContext, PipelineResult

# Runtime compatibility bindings. Stage implementations live in dedicated modules.
_runtime.PipelineContext = PipelineContext
_runtime.PipelineResult = PipelineResult
_runtime.build_base_tasks = build_route_tasks
_runtime.build_secondary_tasks = lambda *args, **kwargs: []
_runtime.compute_pipeline_bbox = compute_pipeline_bbox
_runtime.select_secondary_routes = select_secondary_routes
_runtime._route_inside_bbox = route_inside_bbox
_runtime._apply_dedup_removals = apply_dedup_removals
_runtime._dir_stat_volume_map = dir_stat_volume_map
_runtime._dedup_affected_route_ids = affected_route_ids
_runtime._merge_recomputed_stats = merge_recomputed_stats
_runtime._run_dedup_pass = run_dedup_pass
_runtime.compute_ghs = compute_ghs
_runtime.compute_ghs_s = compute_ghs_s
_runtime.compute_overture = compute_overture
_runtime.compute_poi = compute_poi

__all__ = [name for name in dir(_runtime) if not name.startswith("_")] + [
    "PipelineContext",
    "PipelineResult",
    "build_route_tasks",
    "build_filter_limits",
    "split_active_routes",
    "split_ok_and_errors",
    "apply_limits",
    "compute_pipeline_bbox",
    "route_inside_bbox",
    "select_secondary_routes",
    "affected_route_ids",
    "apply_dedup_removals",
    "dir_stat_volume_map",
    "merge_recomputed_stats",
    "compute_ghs",
    "compute_ghs_s",
    "compute_overture",
    "compute_poi",
    "run_dedup_pass",
]
