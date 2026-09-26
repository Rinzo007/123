from __future__ import annotations

from dataclasses import dataclass
from math import exp


@dataclass(frozen=True, slots=True)
class ModeChoiceConfig:
    scale: float = 0.08
    transit_constant: float = 0.0
    car_constant: float = 0.0
    walk_constant: float = 0.0

    def __post_init__(self) -> None:
        if self.scale <= 0:
            raise ValueError("scale must be positive")


@dataclass(frozen=True, slots=True)
class ModeUtilities:
    walk: float
    car: float
    transit: float


def utilities(
    *,
    walk_time_min: float,
    car_time_min: float,
    transit_time_min: float | None,
    config: ModeChoiceConfig = ModeChoiceConfig(),
) -> ModeUtilities:
    transit = (
        float("-inf")
        if transit_time_min is None
        else config.transit_constant - config.scale * transit_time_min
    )
    return ModeUtilities(
        walk=config.walk_constant - config.scale * walk_time_min,
        car=config.car_constant - config.scale * car_time_min,
        transit=transit,
    )


def probabilities(values: ModeUtilities) -> dict[str, float]:
    finite = {
        "walk": values.walk,
        "car": values.car,
        "transit": values.transit,
    }
    maximum = max(finite.values())
    if maximum == float("-inf"):
        return {"walk": 0.0, "car": 0.0, "transit": 0.0}

    weights = {
        key: 0.0 if value == float("-inf") else exp(value - maximum)
        for key, value in finite.items()
    }
    total = sum(weights.values())
    return {key: weight / total for key, weight in weights.items()}
