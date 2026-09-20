"""Алгоритмический слой пассажиропотока: назначение, ожидание и выбор режима."""

from __future__ import annotations

from .assign import _assign_od
from .kpis import _build_line_kpis
from .mode_choice import (
    _logit_probs,
    _mode_minutes,
    _od_fare_eur,
    _od_fare_min,
)
from .wait import (
    _build_wait_extra,
    _expected_wait_min,
    _reliability_min,
    _run_msa_period,
)

__all__ = [
    "_assign_od",
    "_build_line_kpis",
    "_build_wait_extra",
    "_expected_wait_min",
    "_logit_probs",
    "_mode_minutes",
    "_od_fare_eur",
    "_od_fare_min",
    "_reliability_min",
    "_run_msa_period",
]