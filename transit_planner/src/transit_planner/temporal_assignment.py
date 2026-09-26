from __future__ import annotations

from dataclasses import dataclass, replace

from .assignment import AssignmentConfig, AssignmentResult, assign_demand
from .demand import PeriodODPairDemand, TemporalDemandMatrix
from .network import Network
from .routing import TransitRouter


@dataclass(frozen=True, slots=True)
class PeriodAssignment:
    period_id: str
    demand_trips: float
    result: AssignmentResult


@dataclass(frozen=True, slots=True)
class TemporalAssignmentResult:
    periods: tuple[PeriodAssignment, ...]

    @property
    def total_demand_trips(self) -> float:
        return sum(period.demand_trips for period in self.periods)

    @property
    def total_transit_trips(self) -> float:
        return sum(
            period.result.metrics.transit_trips
            for period in self.periods
        )

    @property
    def total_car_trips(self) -> float:
        return sum(
            period.result.metrics.car_trips
            for period in self.periods
        )

    @property
    def total_walk_trips(self) -> float:
        return sum(
            period.result.metrics.walk_trips
            for period in self.periods
        )


def assign_temporal_demand(
    network: Network,
    demand: TemporalDemandMatrix,
    *,
    config: AssignmentConfig,
    zones: dict | None = None,
    router: TransitRouter | None = None,
) -> TemporalAssignmentResult:
    router = router or TransitRouter(network)

    results: list[PeriodAssignment] = []
    for period_id in network.periods:
        period_pairs = tuple(
            pair for pair in demand.pairs if pair.period_id == period_id
        )
        period_demand = _daily_matrix(period_pairs)
        period_config = replace(config, period_id=period_id)
        assignment = assign_demand(
            network,
            period_demand,
            config=period_config,
            zones=zones,
            router=router,
        )
        results.append(
            PeriodAssignment(
                period_id=period_id,
                demand_trips=period_demand.total_trips_per_day,
                result=assignment,
            )
        )

    return TemporalAssignmentResult(tuple(results))


def _daily_matrix(pairs: tuple[PeriodODPairDemand, ...]):
    from .demand import DemandMatrix, ODPairDemand

    return DemandMatrix(
        tuple(
            ODPairDemand(
                pair.origin_zone_id,
                pair.destination_zone_id,
                pair.trips,
                pair.purpose,
            )
            for pair in pairs
        )
    )
