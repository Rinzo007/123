"""Сетевой слой пассажиропотока: геометрия, маршруты и межостановочные шаги."""

from __future__ import annotations

from .geometry import (
    _find_nearest_stops,
    _stop_key,
    _takt_transfer_penalty_min,
    _transfers_match,
    haversine_meters,
)
from .routes import _build_route_stop_sequence, build_journeys
from .spacing import (
    STOP_SPACING_BANDS,
    StopSpacingVerdict,
    check_stop_spacing,
    stop_spacing_band,
    stop_spacing_verdict,
)

__all__ = [
    "STOP_SPACING_BANDS",
    "StopSpacingVerdict",
    "_build_route_stop_sequence",
    "_find_nearest_stops",
    "_stop_key",
    "_takt_transfer_penalty_min",
    "_transfers_match",
    "build_journeys",
    "check_stop_spacing",
    "haversine_meters",
    "stop_spacing_band",
    "stop_spacing_verdict",
]