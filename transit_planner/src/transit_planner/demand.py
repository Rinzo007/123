from __future__ import annotations

from dataclasses import dataclass

from .demand_profile import DEFAULT_TEMPORAL_DEMAND_PROFILE, TemporalDemandProfile


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


def expand_daily_demand(
    demand: DemandMatrix,
    *,
    profile: TemporalDemandProfile = DEFAULT_TEMPORAL_DEMAND_PROFILE,
) -> TemporalDemandMatrix:
    result: list[PeriodODPairDemand] = []
    for pair in demand.pairs:
        purpose = pair.purpose
        if purpose == "all":
            for purpose_profile in profile.purposes:
                purpose_trips = pair.trips_per_day * purpose_profile.daily_share
                for period_id in profile.period_ids:
                    result.append(
                        PeriodODPairDemand(
                            pair.origin_zone_id,
                            pair.destination_zone_id,
                            period_id,
                            purpose_trips * purpose_profile.period_shares[period_id],
                            purpose_profile.purpose.value,
                        )
                    )
            continue

        purpose_share = profile.purpose_share(purpose)
        if purpose_share <= 0.0:
            raise ValueError(f"Unknown or inactive trip purpose: {purpose}")

        for period_id in profile.period_ids:
            result.append(
                PeriodODPairDemand(
                    pair.origin_zone_id,
                    pair.destination_zone_id,
                    period_id,
                    pair.trips_per_day * profile.period_share(purpose, period_id),
                    purpose,
                )
            )
    return TemporalDemandMatrix(tuple(result))
