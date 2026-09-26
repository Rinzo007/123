from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ODPairDemand:
    origin_zone_id: str
    destination_zone_id: str
    trips_per_day: float
    purpose: str = "all"

    def __post_init__(self) -> None:
        if self.trips_per_day < 0:
            raise ValueError("Trips cannot be negative")


@dataclass(frozen=True, slots=True)
class DemandMatrix:
    pairs: tuple[ODPairDemand, ...]

    @property
    def total_trips_per_day(self) -> float:
        return sum(pair.trips_per_day for pair in self.pairs)
