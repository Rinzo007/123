from __future__ import annotations

from dataclasses import dataclass, replace

from .assignment import AssignmentConfig, AssignmentMetrics, AssignmentResult, DemandLoss, RouteFlow, SectionLoad, StopFlow, assign_demand
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

    @property
    def total_bike_trips(self) -> float:
        return sum(
            period.result.metrics.bike_trips
            for period in self.periods
        )

    def aggregate(self) -> AssignmentResult:
        if not self.periods:
            return AssignmentResult(
                metrics=AssignmentMetrics(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
                route_flows=(),
                section_loads=(),
                stop_flows=(),
                unserved_transit_demand=0.0,
                iterations=0,
                max_load_ratio=0.0,
                loss_reasons=(),
            )

        total = self.total_demand_trips
        transit = self.total_transit_trips
        car = self.total_car_trips
        walk = self.total_walk_trips
        bike = self.total_bike_trips
        weighted_time = sum(
            period.result.metrics.average_transit_time_min * period.result.metrics.transit_trips
            for period in self.periods
        )
        weighted_transfers = sum(
            period.result.metrics.average_transfers * period.result.metrics.transit_trips
            for period in self.periods
        )

        route_acc: dict[str, list[float]] = {}
        for period in self.periods:
            for flow in period.result.route_flows:
                values = route_acc.setdefault(flow.route_id, [0.0, 0.0])
                values[0] += flow.boardings
                values[1] += flow.passenger_section_traversals

        section_acc: dict[tuple[str, str, str], list[float]] = {}
        for period in self.periods:
            for section in period.result.section_loads:
                values = section_acc.setdefault((section.route_id, section.from_stop_id, section.to_stop_id), [0.0, 0.0, 0.0])
                values[0] += section.passengers
                values[1] += section.capacity
                values[2] = max(values[2], section.load_ratio)

        stop_acc: dict[str, list[float]] = {}
        for period in self.periods:
            for stop in period.result.stop_flows:
                values = stop_acc.setdefault(stop.stop_id, [0.0, 0.0, 0.0, 0.0, 0.0])
                values[0] += stop.boardings
                values[1] += stop.alightings
                values[2] += stop.transfers
                values[3] += stop.dwell_seconds
                values[4] = max(values[4], stop.platform_m)

        losses: dict[str, float] = {}
        for period in self.periods:
            for loss in period.result.loss_reasons:
                losses[loss.reason] = losses.get(loss.reason, 0.0) + loss.trips

        return AssignmentResult(
            metrics=AssignmentMetrics(
                total_trips=total,
                transit_trips=transit,
                car_trips=car,
                walk_trips=walk,
                transit_share=0.0 if total <= 0 else transit / total,
                average_transit_time_min=0.0 if transit <= 0 else weighted_time / transit,
                average_transfers=0.0 if transit <= 0 else weighted_transfers / transit,
                bike_trips=bike,
            ),
            route_flows=tuple(RouteFlow(route_id, values[0], values[1]) for route_id, values in sorted(route_acc.items())),
            section_loads=tuple(SectionLoad(route_id, from_id, to_id, values[0], values[1]) for (route_id, from_id, to_id), values in sorted(section_acc.items())),
            stop_flows=tuple(StopFlow(stop_id, values[0], values[1], values[2], values[3], values[4]) for stop_id, values in sorted(stop_acc.items())),
            unserved_transit_demand=sum(period.result.unserved_transit_demand for period in self.periods),
            iterations=max(period.result.iterations for period in self.periods),
            max_load_ratio=max(period.result.max_load_ratio for period in self.periods),
            loss_reasons=tuple(DemandLoss(reason, trips) for reason, trips in sorted(losses.items())),
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
