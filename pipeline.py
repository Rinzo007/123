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
from .pipeline_filtering import (
    apply_limits,
    build_filter_limits,
    split_active_routes,
    split_ok_and_errors,
)
from .pipeline_loading import build_route_tasks
from .pipeline_output import (
    build_heatmap,
    compute_stop_volumes,
    generate_routes_network,
    gen_route_count_formula,
    stops_area_km2,
)
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
_runtime._stops_area_km2 = stops_area_km2
_runtime.compute_stop_volumes = compute_stop_volumes
_runtime.gen_route_count_formula = gen_route_count_formula
_runtime.generate_routes_network = generate_routes_network
_runtime.build_heatmap = build_heatmap

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
    "stops_area_km2",
    "compute_stop_volumes",
    "gen_route_count_formula",
    "generate_routes_network",
    "build_heatmap",
]
