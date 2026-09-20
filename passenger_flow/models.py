"""Совместимый реэкспорт типов пассажиропотока (старый путь импорта).

Фактическое определение — ``.base.models``; данный модуль сохранён, чтобы
внешние импорты ``passenger_flow.models`` (config, od) продолжали работать.
"""

from __future__ import annotations

from typing import Any

from .base.models import *
from .base.models import (
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

__all__ = [
    "PURPOSE_DEFAULTS",
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

# Runtime-compatible structural type alias used by passenger_flow.core.\nRouteLike = Any\n