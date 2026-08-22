"""Типы состояния и результата pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .cache import JsonCache
from .http_client import SessionProvider
from .metrics import BuiltSStats, GhsStats, OvertureStats, PoiStats
from .models import FilterLimits, RouteData
from .report import Reporter

@dataclass
class PipelineContext:
    cache: JsonCache
    sessions: SessionProvider
    reporter: Reporter = field(default_factory=Reporter)

@dataclass
class PipelineResult:
    city_slug: str
    city_title: str
    base_task_count: int
    secondary_task_count: int
    base_routes: list[RouteData]
    secondary_routes: list[RouteData]
    all_routes: list[RouteData]
    ok_routes: list[RouteData]
    bad_routes: list[RouteData]
    bbox: tuple[float, float, float, float] | None
    limits: FilterLimits
    excluded_routes: list[tuple[RouteData, str]]
    excluded_counts: dict[str, int]
    skipped_inactive: int
    ghs_stats: dict[int, GhsStats]
    ghs_meta: dict[str, Any] | None
    ghs_dir_stats: dict[tuple[int, int], GhsStats]
    built_s_stats: dict[int, BuiltSStats]
    built_s_meta: dict[str, Any] | None
    built_s_dir_stats: dict[tuple[int, int], BuiltSStats]
    overture_stats: dict[int, OvertureStats]
    overture_meta: dict[str, Any] | None
    overture_dir_stats: dict[tuple[int, int], OvertureStats]
    poi_stats: dict[int, PoiStats]
    poi_values: dict[str, float]
    poi_dir_stats: dict[tuple[int, int], PoiStats]
    dedup_removed: list[dict[str, Any]] | None
    dedup_analysis: dict[str, Any] | None
    net_metrics: dict[str, Any] | None
    unique_stops: dict[str, dict[str, Any]]
    generated_routes: list[dict[str, Any]]
    stop_volumes: dict[str, float] | None
    heatmap: dict[str, Any] | None

__all__ = ["PipelineContext", "PipelineResult"]
