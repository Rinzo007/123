from __future__ import annotations

from dataclasses import dataclass
from math import exp, inf, isclose

from .city import DemandZone
from .demand import DemandMatrix, ODPairDemand


@dataclass(frozen=True, slots=True)
class GravityParameters:
    impedance_minutes: float = 20.0
    decay: float = 0.08
    intrazonal_factor: float = 0.5

    def __post_init__(self) -> None:
        if self.impedance_minutes <= 0:
            raise ValueError("impedance_minutes must be positive")
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
    """Generate a balanced synthetic OD matrix.

    Productions are population * trip_rate and attractions are proportional to jobs.
    When no jobs exist, population is used as the attraction proxy.
    """
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
            distance = _distance(origin, destination)
            impedance = (
                parameters.intrazonal_factor
                if distance == 0
                else distance / parameters.impedance_minutes
            )
            friction = exp(-parameters.decay * impedance)
            weight = attraction_base[destination] * friction
            raw.append((origin.id, destination.id, weight))

    pairs: list[ODPairDemand] = []
    by_origin: dict[str, float] = {}
    for origin_id, _, weight in raw:
        by_origin[origin_id] = by_origin.get(origin_id, 0.0) + weight

    for origin_id, destination_id, weight in raw:
        total = by_origin[origin_id]
        trips = 0.0 if isclose(total, 0.0, abs_tol=1e-12) else productions[origin_id] * weight / total
        pairs.append(ODPairDemand(origin_id, destination_id, trips))

    return DemandMatrix(tuple(pairs))


def _distance(a: DemandZone, b: DemandZone) -> float:
    return ((a.centroid_x - b.centroid_x) ** 2 + (a.centroid_y - b.centroid_y) ** 2) ** 0.5
