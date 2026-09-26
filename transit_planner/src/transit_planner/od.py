from __future__ import annotations

from dataclasses import dataclass
from math import exp, isclose

from .city import DemandZone
from .demand_profile import DEFAULT_TEMPORAL_DEMAND_PROFILE, TemporalDemandProfile, TripPurpose
from .demand import DemandMatrix, ODPairDemand


@dataclass(frozen=True, slots=True)
class GravityParameters:
    speed_kph: float = 30.0
    decay: float = 0.08
    intrazonal_factor: float = 0.5

    def __post_init__(self) -> None:
        if self.speed_kph <= 0:
            raise ValueError("speed_kph must be positive")
        if self.decay <= 0:
            raise ValueError("decay must be positive")
        if not 0 < self.intrazonal_factor <= 1:
            raise ValueError("intrazonal_factor must be in (0, 1]")


def gravity_od(
    zones: tuple[DemandZone, ...],
    *,
    parameters: GravityParameters = GravityParameters(),
    trip_rate: float = 0.12,
) -> DemandMatrix:
    if trip_rate < 0:
        raise ValueError("trip_rate cannot be negative")

    productions = {z.id: max(0.0, z.population * trip_rate) for z in zones}
    attraction_base = {z.id: max(0.0, z.jobs) for z in zones}
    if sum(attraction_base.values()) <= 0:
        attraction_base = {z.id: max(0.0, z.population) for z in zones}
    total_attraction = sum(attraction_base.values())

    if not zones or total_attraction <= 0:
        return DemandMatrix(())

    raw: list[tuple[str, str, float]] = []
    for origin in zones:
        for destination in zones:
            distance_m = _distance_m(origin, destination)
            impedance_minutes = (
                parameters.intrazonal_factor
                if distance_m == 0
                else distance_m / 1000.0 / parameters.speed_kph * 60.0
            )
            friction = exp(-parameters.decay * impedance_minutes)
            weight = attraction_base[destination.id] * friction
            raw.append((origin.id, destination.id, weight))

    pairs: list[ODPairDemand] = []
    by_origin: dict[str, float] = {}
    for origin_id, _, weight in raw:
        by_origin[origin_id] = by_origin.get(origin_id, 0.0) + weight

    for origin_id, destination_id, weight in raw:
        total = by_origin[origin_id]
        trips = (
            0.0
            if isclose(total, 0.0, abs_tol=1e-12)
            else productions[origin_id] * weight / total
        )
        pairs.append(ODPairDemand(origin_id, destination_id, trips))

    return DemandMatrix(tuple(pairs))

