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


@dataclass(frozen=True, slots=True)
class PeriodODPairDemand:
    origin_zone_id: str
    destination_zone_id: str
    period_id: str
    trips: float
    purpose: str = "all"

    def __post_init__(self) -> None:
        if self.trips < 0:
            raise ValueError("Trips cannot be negative")
        if not self.period_id.strip():
            raise ValueError("period_id cannot be empty")


@dataclass(frozen=True, slots=True)
class TemporalDemandMatrix:
    pairs: tuple[PeriodODPairDemand, ...]

    @property
    def total_trips(self) -> float:
        return sum(pair.trips for pair in self.pairs)

    def by_period(self, period_id: str) -> tuple[PeriodODPairDemand, ...]:
        return tuple(pair for pair in self.pairs if pair.period_id == period_id)

    def by_purpose(self, purpose: str) -> tuple[PeriodODPairDemand, ...]:
        return tuple(pair for pair in self.pairs if pair.purpose == purpose)

    def period_totals(self) -> dict[str, float]:
        totals: dict[str, float] = {}
        for pair in self.pairs:
            totals[pair.period_id] = totals.get(pair.period_id, 0.0) + pair.trips
        return totals
