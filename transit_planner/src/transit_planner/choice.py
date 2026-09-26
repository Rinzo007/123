from __future__ import annotations

from dataclasses import dataclass
from math import exp

from .reference_model import (
    REFERENCE_CAR,
    REFERENCE_MOBILITY,
    REFERENCE_TRANSFER,
    REFERENCE_VOT_S_PER_EUR,
)


@dataclass(frozen=True, slots=True)
class ChoiceConfig:
    """Mode-choice parameters used by the planner's main demand model."""

    value_of_time_s_per_eur: float = REFERENCE_VOT_S_PER_EUR
    transit_constant: float = 0.0
    car_constant: float = 0.0
    walk_constant: float = 0.0
    bike_constant: float = 0.0
    transit_fare_weight: float = 0.0
    transit_wait_weight: float = REFERENCE_TRANSFER.wait_multiplier
    car_cost_per_km_eur: float = REFERENCE_CAR.cost_per_km_eur
    car_parking_eur: float = REFERENCE_CAR.parking_eur
    car_parking_minutes: float = REFERENCE_CAR.parking_s / 60.0
    car_circuity: float = REFERENCE_CAR.circuity
    walk_circuity: float = 1.25 * REFERENCE_TRANSFER.walk_multiplier
    bike_circuity: float = 1.25
    bike_cost_per_km_eur: float = REFERENCE_MOBILITY.two_wheel_per_km_eur
    bike_fixed_minutes: float = REFERENCE_CAR.parking_s / 60.0
    bike_max_distance_m: float = REFERENCE_MOBILITY.two_wheel_reach_m
    walk_speed_kph: float = 5.0
    bike_speed_kph: float = REFERENCE_MOBILITY.two_wheel_speed_kph

    def __post_init__(self) -> None:
        if self.value_of_time_s_per_eur <= 0:
            raise ValueError("value_of_time_s_per_eur must be positive")
        if self.transit_fare_weight < 0 or self.transit_wait_weight < 0:
            raise ValueError("Transit weights cannot be negative")
        if self.car_cost_per_km_eur < 0 or self.bike_cost_per_km_eur < 0:
            raise ValueError("Mode operating cost cannot be negative")
        if self.car_parking_eur < 0 or self.car_parking_minutes < 0:
            raise ValueError("Parking cost and time cannot be negative")
        if self.car_circuity <= 0 or self.walk_circuity <= 0 or self.bike_circuity <= 0:
            raise ValueError("Circuity factors must be positive")
        if self.bike_fixed_minutes < 0 or self.bike_max_distance_m < 0:
            raise ValueError("Bike fixed time and maximum distance cannot be negative")
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
    transit_wait_min: float = 0.0,
    transit_fare: float = 0.0,
    car_distance_km: float = 0.0,
    bike_distance_km: float | None = None,
    config: ChoiceConfig = ChoiceConfig(),
) -> ModeUtilities:
    coefficient = config.time_coefficient
    transit = (
        float("-inf")
        if transit_time_min is None
        else (
            config.transit_constant
            - coefficient * (
                transit_time_min
                + config.transit_wait_weight * max(0.0, transit_wait_min)
            )
            - config.transit_fare_weight * transit_fare
        )
    )
    bike_distance_km = 0.0 if bike_distance_km is None else max(0.0, bike_distance_km)
    if bike_distance_km > config.bike_max_distance_m / 1000.0:
        bike = float("-inf")
    else:
        bike_time = walk_time_min if bike_time_min is None else bike_time_min
        bike_generalized_minutes = (
            config.bike_fixed_minutes
            + config.bike_circuity * bike_time
            + bike_distance_km * config.bike_cost_per_km_eur
            * config.value_of_time_s_per_eur / 60.0
        )
        bike = config.bike_constant - coefficient * bike_generalized_minutes

    walk_generalized_minutes = config.walk_circuity * walk_time_min
    car_generalized_minutes = (
        config.car_circuity * car_time_min
        + config.car_parking_minutes
        + (
            car_distance_km * config.car_cost_per_km_eur
            + config.car_parking_eur
        ) * config.value_of_time_s_per_eur / 60.0
    )
    return ModeUtilities(
        walk=config.walk_constant - coefficient * walk_generalized_minutes,
        car=config.car_constant - coefficient * car_generalized_minutes,
        transit=transit,
        bike=bike,
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
