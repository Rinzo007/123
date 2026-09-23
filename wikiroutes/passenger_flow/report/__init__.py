"""Слой отчётности: сборка результата, сравнение с эталоном и калибровка."""

from __future__ import annotations

from .assembly import assemble_flow_result
from .baseline import (
    BaselineComparison,
    BaselineLine,
    HeadwayStats,
    ScheduleLine,
    compare_schedules,
    load_baseline,
    per_mode_headway_stats,
    render_report,
)
from .tune import TunedLine, TuneResult, render_tune_report, tune_headways

__all__ = [
    "BaselineComparison",
    "BaselineLine",
    "HeadwayStats",
    "ScheduleLine",
    "TuneResult",
    "TunedLine",
    "assemble_flow_result",
    "compare_schedules",
    "load_baseline",
    "per_mode_headway_stats",
    "render_report",
    "render_tune_report",
    "tune_headways",
]