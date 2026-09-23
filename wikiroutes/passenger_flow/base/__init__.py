"""Базовый слой пассажиропотока: типы результата и константы Takt."""

from __future__ import annotations

from .models import (
    PURPOSE_DEFAULTS,
    VEHICLE_DEFAULTS,
    FlowResult,
    LineResult,
    ModeChoiceConfig,
    PassengerFlowError,
    Period,
    PeriodFlow,
    Purpose,
    VehicleSpec,
    vehicle_spec_for_route_type,
)
from .takt import TAKT_FLEET, TAKT_PERIODS

__all__ = [
    "PURPOSE_DEFAULTS",
    "TAKT_FLEET",
    "TAKT_PERIODS",
    "VEHICLE_DEFAULTS",
    "FlowResult",
    "LineResult",
    "ModeChoiceConfig",
    "PassengerFlowError",
    "Period",
    "PeriodFlow",
    "Purpose",
    "VehicleSpec",
    "vehicle_spec_for_route_type",
]