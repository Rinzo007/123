from __future__ import annotations

from dataclasses import dataclass
from math import exp


@dataclass(frozen=True, slots=True)
class ChoiceConfig:
    """Mode-choice parameters used by the planner's main demand model."""

    value_of_time_s_per_eur: float = 360.0
    transit_constant: float = 0.0
    car_constant: float = 0.0
    walk_constant: float = 0.0
    bike_constant: float = 0.0
    transit_fare_weight: float = 0.0
    car_cost_per_km_eur: float = 0.25
    car_parking_eur: float = 1.5
    car_parking_minutes: float = 4.0
    walk_speed_kph: float = 5.0
    bike_speed_kph: float = 15.12

    def __post_init__(self) -> None:
        if self.value_of_time_s_per_eur <= 0:
            raise ValueError("value_of_time_s_per_eur must be positive")
        if self.transit_fare_weight < 0:
            raise ValueError("transit_fare_weight cannot be negative")
        if self.car_cost_per_km_eur < 0:
            raise ValueError("car_cost_per_km_eur cannot be negative")
        if self.car_parking_eur < 0 or self.car_parking_minutes < 0:
            raise ValueError("Parking cost and time cannot be negative")
        if self.walk_speed_kph <= 0 or self.bike_speed_kph <= 0:
            raise ValueError("Walking and cycling speeds must be positive")

    @property
    def time_coefficient(self) -> float:
        return 60.0 / self.value_of_time_s_per_eur


@dataclass(frozen=True, slots=True)
class ModeUtilities:
    walk: float
    car: float
    transit: float
    bike: float


def utilities(
    *,
    walk_time_min: float,
    car_time_min: float,
    transit_time_min: float | None,
    bike_time_min: float | None = None,
    transit_fare: float = 0.0,
    car_distance_km: float = 0.0,
    config: ChoiceConfig = ChoiceConfig(),
) -> ModeUtilities:
    coefficient = config.time_coefficient
    transit = (
        float("-inf")
        if transit_time_min is None
        else (
            config.transit_constant
            - coefficient * transit_time_min
            - config.transit_fare_weight * transit_fare
        )
    )
    bike_time = (
        walk_time_min
        if bike_time_min is None
        else bike_time_min
    )
    car_generalized_cost = (
        car_time_min
        + config.car_parking_minutes
        + (
            car_distance_km * config.car_cost_per_km_eur
            + config.car_parking_eur
        ) * config.value_of_time_s_per_eur / 3600.0
    )
    return ModeUtilities(
        walk=config.walk_constant - coefficient * walk_time_min,
        car=config.car_constant - coefficient * car_generalized_cost,
        transit=transit,
        bike=config.bike_constant - coefficient * bike_time,
    )


def probabilities(values: ModeUtilities) -> dict[str, float]:
    available = {
        "walk": values.walk,
        "car": values.car,
        "transit": values.transit,
        "bike": values.bike,
    }
    maximum = max(available.values())
    if maximum == float("-inf"):
        return {key: 0.0 for key in available}

    weights = {
        key: 0.0 if value == float("-inf") else exp(value - maximum)
        for key, value in available.items()
    }
    total = sum(weights.values())
    if total <= 0:
        return {key: 0.0 for key in available}
    return {key: weight / total for key, weight in weights.items()}
