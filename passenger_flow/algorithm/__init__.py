"""Алгоритмический слой пассажиропотока: назначение, ожидание и выбор режима."""

from __future__ import annotations

from .assign import _assign_od
from .kpis import _build_line_kpis
from .mode_choice import (
    _od_fare_eur,
    _takt_mode_shares,
    _takt_route_choice,
    _takt_route_probs,
)
from .wait import (
    _build_crowd_state,
    _build_wait_extra,
    _expected_wait_min,
    _reliability_min,
    _run_msa_period,
)

__all__ = [
    "_assign_od",
    "_build_crowd_state",
    "_build_line_kpis",
    "_build_wait_extra",
    "_expected_wait_min",
    "_od_fare_eur",
    "_reliability_min",
    "_run_msa_period",
    "_takt_mode_shares",
    "_takt_route_choice",
    "_takt_route_probs",
]
