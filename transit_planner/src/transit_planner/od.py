from __future__ import annotations

from dataclasses import dataclass
from math import exp, isclose

from .city import DemandZone
from .demand_profile import DEFAULT_TEMPORAL_DEMAND_PROFILE, TemporalDemandProfile, TripPurpose
from .demand import DemandMatrix, ODPairDemand


@dataclass(frozen=True, slots=True)
class GravityParameters:
    reference_speed_kph: float = 30.0
    decay: float = 0.08
    intrazonal_factor: float = 0.5

    def __post_init__(self) -> None:
        if self.reference_speed_kph <= 0:
            raise ValueError("reference_speed_kph must be positive")
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
                else distance_m / 1000.0 / parameters.reference_speed_kph * 60.0
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



def purpose_gravity_od(
    zones: tuple[DemandZone, ...],
    *,
    profile: TemporalDemandProfile = DEFAULT_TEMPORAL_DEMAND_PROFILE,
    trip_rate: float = 0.12,
    decay: float = 0.08,
    reference_speed_kph: float = 30.0,
) -> DemandMatrix:
    if trip_rate < 0 or decay <= 0 or reference_speed_kph <= 0:
        raise ValueError('trip_rate must be non-negative; decay and speed must be positive')
    if not zones:
        return DemandMatrix(())

    pairs: list[ODPairDemand] = []
    for purpose_profile in profile.purposes:
        productions = {
            zone.id: max(0.0, zone.population * trip_rate * purpose_profile.daily_share)
            for zone in zones
        }
        attractions = {zone.id: _purpose_attraction(zone, purpose_profile.purpose) for zone in zones}
        if sum(attractions.values()) <= 0:
            continue

        for origin in zones:
            weights: list[tuple[str, float]] = []
            for destination in zones:
                distance_m = _distance_m(origin, destination)
                minutes = (
                    distance_m / 1000.0 / reference_speed_kph * 60.0
                    if distance_m > 0
                    else 0.5
                )
                weight = attractions[destination.id] * exp(-decay * minutes)
                weights.append((destination.id, weight))

            total_weight = sum(weight for _, weight in weights)
            if total_weight <= 0 or productions[origin.id] <= 0:
                continue
            for destination_id, weight in weights:
                trips = productions[origin.id] * weight / total_weight
                if trips > 0:
                    pairs.append(
                        ODPairDemand(
                            origin.id,
                            destination_id,
                            trips,
                            purpose_profile.purpose.value,
                        )
                    )

    return DemandMatrix(tuple(pairs))


def _purpose_attraction(zone: DemandZone, purpose: TripPurpose) -> float:
    explicit = zone.attractions.get(purpose.value, 0.0)
    if explicit > 0:
        return explicit
    if purpose == TripPurpose.WORK:
        return max(0.0, zone.jobs)
    return max(0.0, zone.population)

def _distance_m(a: DemandZone, b: DemandZone) -> float:
    return (
        (a.centroid_x - b.centroid_x) ** 2
        + (a.centroid_y - b.centroid_y) ** 2
    ) ** 0.5
