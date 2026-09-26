from __future__ import annotations

from dataclasses import dataclass
from math import ceil, sqrt

from .city import DemandZone
from .demand import DemandMatrix, ODPairDemand
from .choice import ChoiceConfig, probabilities, utilities
from .network import Network
from .routing import Journey, TransitRouter


@dataclass(frozen=True, slots=True)
class SectionLoad:
    route_id: str
    from_stop_id: str
    to_stop_id: str
    passengers: float
    capacity: float

    @property
    def load_ratio(self) -> float:
        return 0.0 if self.capacity <= 0 else self.passengers / self.capacity


@dataclass(frozen=True, slots=True)
class RouteFlow:
    route_id: str
    boardings: float
    passenger_section_traversals: float


@dataclass(frozen=True, slots=True)
class StopFlow:
    stop_id: str
    boardings: float
    alightings: float
    transfers: float


@dataclass(frozen=True, slots=True)
class AssignmentMetrics:
    total_trips: float
    transit_trips: float
    car_trips: float
    walk_trips: float
    transit_share: float
    average_transit_time_min: float
    average_transfers: float
    bike_trips: float = 0.0


@dataclass(frozen=True, slots=True)
class DemandLoss:
    reason: str
    trips: float


@dataclass(frozen=True, slots=True)
class AssignmentResult:
    metrics: AssignmentMetrics
    route_flows: tuple[RouteFlow, ...]
    section_loads: tuple[SectionLoad, ...]
    stop_flows: tuple[StopFlow, ...]
    unserved_transit_demand: float
    iterations: int
    max_load_ratio: float
    loss_reasons: tuple[DemandLoss, ...] = ()


@dataclass(frozen=True, slots=True)
class AssignmentConfig:
    period_id: str
    car_speed_kph: float = 30.0
    walking_speed_kph: float = 5.0
    bike_speed_kph: float = 15.0
    choice: ChoiceConfig = ChoiceConfig()
    transfer_penalty_min: float = 5.0
    crowding_penalty_min: float = 20.0
    crowding_start_ratio: float = 0.85
    iterations: int = 6
    damping: float = 0.5
    transit_fare: float = 0.0
    convergence_tolerance: float = 1e-4
    max_access_distance_m: float = 1500.0

    def __post_init__(self) -> None:
        if self.car_speed_kph <= 0 or self.walking_speed_kph <= 0 or self.bike_speed_kph <= 0:
            raise ValueError("Speeds must be positive")
        if self.transfer_penalty_min < 0:
            raise ValueError("transfer_penalty_min cannot be negative")
        if self.crowding_penalty_min < 0:
            raise ValueError("crowding_penalty_min cannot be negative")
        if self.crowding_start_ratio < 0:
            raise ValueError("crowding_start_ratio cannot be negative")
        if self.iterations <= 0:
            raise ValueError("iterations must be positive")
        if not 0 < self.damping <= 1:
            raise ValueError("damping must be in (0, 1]")
        if self.transit_fare < 0:
            raise ValueError("transit_fare cannot be negative")
        if self.convergence_tolerance <= 0:
            raise ValueError("convergence_tolerance must be positive")
        if self.max_access_distance_m < 0:
            raise ValueError("max_access_distance_m cannot be negative")


def assign_demand(
    network: Network,
    demand: DemandMatrix,
    *,
    config: AssignmentConfig,
    zones: dict[str, DemandZone] | None = None,
    router: TransitRouter | None = None,
) -> AssignmentResult:
    router = router or TransitRouter(network)
    zones = zones or {}
    zone_stops = {
        zone_id: _nearest_stop_id(network, zones[zone_id], config.max_access_distance_m)
        for zone_id in zones
    }

    route_penalties: dict[str, float] = {}
    snapshot: _FlowSnapshot | None = None
    converged_after = config.iterations

    for iteration in range(1, config.iterations + 1):
        snapshot = _assign_once(
            network,
            demand,
            router,
            config,
            zones,
            zone_stops,
            route_penalties,
        )
        route_target_penalties = _route_crowding_penalties(snapshot.section_loads, config)
        max_delta = _max_penalty_delta(route_penalties, route_target_penalties)
        route_penalties = {
            route_id: (
                route_penalties.get(route_id, 0.0) * config.damping
                + target * (1.0 - config.damping)
            )
            for route_id, target in route_target_penalties.items()
        }
        if iteration > 1 and max_delta <= config.convergence_tolerance:
            converged_after = iteration
            break

    assert snapshot is not None
    return AssignmentResult(
        metrics=snapshot.metrics,
        route_flows=snapshot.route_flows,
        section_loads=snapshot.section_loads,
        stop_flows=snapshot.stop_flows,
        unserved_transit_demand=snapshot.unserved,
        iterations=converged_after,
        max_load_ratio=max((s.load_ratio for s in snapshot.section_loads), default=0.0),
        loss_reasons=snapshot.loss_reasons,
    )


@dataclass(frozen=True, slots=True)
class _FlowSnapshot:
    metrics: AssignmentMetrics
    route_flows: tuple[RouteFlow, ...]
    section_loads: tuple[SectionLoad, ...]
    stop_flows: tuple[StopFlow, ...]
    unserved: float
    loss_reasons: tuple[DemandLoss, ...]


def _assign_once(
    network: Network,
    demand: DemandMatrix,
    router: TransitRouter,
    config: AssignmentConfig,
    zones: dict[str, DemandZone],
    zone_stops: dict[str, str | None],
    route_penalties: dict[str, float],
) -> _FlowSnapshot:
    section_flow: dict[tuple[str, str, str], float] = {}
    section_capacity = _section_capacities(network, config.period_id)
    route_boardings: dict[str, float] = {}
    route_traversals: dict[str, float] = {}
    stop_boardings: dict[str, float] = {}
    stop_alightings: dict[str, float] = {}
    stop_transfers: dict[str, float] = {}
    total_transit = total_car = total_walk = total_bike = 0.0
    weighted_transit_time = weighted_transfers = 0.0
    unserved = 0.0
    loss_reasons: dict[str, float] = {}

    for pair in demand.pairs:
        trips = pair.trips_per_day
        if trips <= 0:
            continue

        distance_m = _distance_between_zones(pair, zones)
        walk_time = distance_m / 1000.0 / config.walking_speed_kph * 60.0
        car_time = distance_m / 1000.0 / config.car_speed_kph * 60.0
        bike_time = distance_m / 1000.0 / config.bike_speed_kph * 60.0

        origin_stop_id = zone_stops.get(pair.origin_zone_id) or _resolve_stop(network, pair.origin_zone_id)
        destination_stop_id = zone_stops.get(pair.destination_zone_id) or _resolve_stop(network, pair.destination_zone_id)

        journey: Journey | None = None
        if origin_stop_id and destination_stop_id:
            candidate = router.shortest(
                network.stops[origin_stop_id],
                network.stops[destination_stop_id],
                period_id=config.period_id,
                route_penalties=route_penalties,
            )
            if candidate is not None and any(leg.kind == "transit" for leg in candidate.legs):
                journey = candidate

        transit_time = None if journey is None else (
            journey.duration_min + journey.transfers * config.transfer_penalty_min
        )
        probs = probabilities(
            utilities(
                walk_time_min=walk_time,
                car_time_min=car_time,
                transit_time_min=transit_time,
                bike_time_min=bike_time,
                config=config.choice,
            )
        )

        transit_trips = trips * probs["transit"]
        car_trips = trips * probs["car"]
        walk_trips = trips * probs["walk"]
        bike_trips = trips * probs["bike"]
        total_transit += transit_trips
        total_car += car_trips
        total_walk += walk_trips
        total_bike += bike_trips

        if journey is None:
            unserved += transit_trips
            if transit_trips > 0:
                loss_reasons["no_service"] = loss_reasons.get("no_service", 0.0) + transit_trips
            continue

        lost_trips = max(0.0, trips - transit_trips)
        if lost_trips > 0:
            reason = _classify_demand_loss(
                transit_time=transit_time or 0.0,
                walk_time=walk_time,
                car_time=car_time,
                bike_time=bike_time,
                transfers=journey.transfers,
                transit_fare=config.transit_fare,
                fare_weight=config.choice.transit_fare_weight,
                route_penalized=any(
                    route_penalties.get(leg.route_id or "", 0.0) > 0.0
                    for leg in journey.legs
                    if leg.kind == "transit"
                ),
            )
            loss_reasons[reason] = loss_reasons.get(reason, 0.0) + lost_trips

        weighted_transit_time += transit_trips * transit_time if transit_time is not None else 0.0
        weighted_transfers += transit_trips * journey.transfers

        for index, leg in enumerate(journey.legs):
            if leg.kind != "transit" or leg.route_id is None:
                continue
            key = (leg.route_id, leg.from_id, leg.to_id)
            section_flow[key] = section_flow.get(key, 0.0) + transit_trips
            route_traversals[leg.route_id] = route_traversals.get(leg.route_id, 0.0) + transit_trips

            previous_leg = journey.legs[index - 1] if index else None
            next_leg = journey.legs[index + 1] if index + 1 < len(journey.legs) else None
            previous_same_route = (
                previous_leg is not None
                and previous_leg.kind == "transit"
                and previous_leg.route_id == leg.route_id
            )
            next_same_route = (
                next_leg is not None
                and next_leg.kind == "transit"
                and next_leg.route_id == leg.route_id
            )

            if not previous_same_route:
                route_boardings[leg.route_id] = route_boardings.get(leg.route_id, 0.0) + transit_trips
                stop_boardings[leg.from_id] = stop_boardings.get(leg.from_id, 0.0) + transit_trips
                if previous_leg is not None and previous_leg.kind == "walk":
                    stop_transfers[leg.from_id] = stop_transfers.get(leg.from_id, 0.0) + transit_trips

            if not next_same_route:
                stop_alightings[leg.to_id] = stop_alightings.get(leg.to_id, 0.0) + transit_trips
                if next_leg is not None and next_leg.kind == "walk":
                    stop_transfers[leg.to_id] = stop_transfers.get(leg.to_id, 0.0) + transit_trips

    section_loads = tuple(
        SectionLoad(
            route_id=route_id,
            from_stop_id=from_id,
            to_stop_id=to_id,
            passengers=section_flow.get((route_id, from_id, to_id), 0.0),
            capacity=capacity,
        )
        for route_id, from_id, to_id, capacity in section_capacity
    )
    route_flows = tuple(
        RouteFlow(
            route_id=route_id,
            boardings=route_boardings.get(route_id, 0.0),
            passenger_section_traversals=route_traversals.get(route_id, 0.0),
        )
        for route_id in network.routes
    )
    stop_flows = tuple(
        StopFlow(
            stop_id=stop_id,
            boardings=stop_boardings.get(stop_id, 0.0),
            alightings=stop_alightings.get(stop_id, 0.0),
            transfers=stop_transfers.get(stop_id, 0.0),
        )
        for stop_id in network.stops
    )

    total = demand.total_trips_per_day
    metrics = AssignmentMetrics(
        total_trips=total,
        transit_trips=total_transit,
        car_trips=total_car,
        walk_trips=total_walk,
        transit_share=0.0 if total <= 0 else total_transit / total,
        average_transit_time_min=0.0 if total_transit <= 0 else weighted_transit_time / total_transit,
        average_transfers=0.0 if total_transit <= 0 else weighted_transfers / total_transit,
        bike_trips=total_bike,
    )
    losses = tuple(
        DemandLoss(reason, trips)
        for reason, trips in sorted(loss_reasons.items())
    )
    return _FlowSnapshot(metrics, route_flows, section_loads, stop_flows, unserved, losses)


def _classify_demand_loss(
    *,
    transit_time: float,
    walk_time: float,
    car_time: float,
    bike_time: float,
    transfers: int,
    transit_fare: float,
    fare_weight: float,
    route_penalized: bool,
) -> str:
    if route_penalized:
        return "crowding"
    best_alternative = min(walk_time, car_time, bike_time)
    if transit_fare > 0.0 and fare_weight * transit_fare >= 0.5 * transit_time:
        return "fare"
    if transfers > 0 and transit_time > best_alternative:
        return "transfer"
    if transit_time > best_alternative:
        return "travel_time"
    return "mode_competition"

def _section_capacities(
    network: Network,
    period_id: str,
) -> tuple[tuple[str, str, str, float], ...]:
    capacities: dict[tuple[str, str, str], float] = {}
    period = network.periods[period_id]
    duration = period.end_minute - period.start_minute

    for service in network.services.values():
        headway = service.headway_by_period.get(period_id)
        if headway is None:
            continue
        departures = ceil(duration / headway)
        vehicle_capacity = network.vehicle_types[service.vehicle_type_id].capacity
        capacity = departures * vehicle_capacity
        route = network.routes[service.route_id]
        for from_id, to_id in zip(route.stop_ids, route.stop_ids[1:]):
            key = (route.id, from_id, to_id)
            reverse_key = (route.id, to_id, from_id)
            capacities[key] = capacities.get(key, 0.0) + capacity
            capacities[reverse_key] = capacities.get(reverse_key, 0.0) + capacity

    return tuple(
        (route_id, from_stop_id, to_stop_id, capacity)
        for (route_id, from_stop_id, to_stop_id), capacity in capacities.items()
    )


def _route_crowding_penalties(
    sections: tuple[SectionLoad, ...],
    config: AssignmentConfig,
) -> dict[str, float]:
    penalties: dict[str, float] = {}
    for section in sections:
        excess = max(0.0, section.load_ratio - config.crowding_start_ratio)
        penalty = excess * config.crowding_penalty_min
        penalties[section.route_id] = max(penalties.get(section.route_id, 0.0), penalty)
    return penalties


def _nearest_stop_id(
    network: Network,
    zone: DemandZone,
    max_distance_m: float,
) -> str | None:
    best_id: str | None = None
    best_distance = max_distance_m
    for stop in network.stops.values():
        distance = sqrt(
            (stop.location.x - zone.centroid_x) ** 2
            + (stop.location.y - zone.centroid_y) ** 2
        )
        if distance <= best_distance:
            best_distance = distance
            best_id = stop.id
    return best_id


def _resolve_stop(network: Network, identifier: str) -> str | None:
    return identifier if identifier in network.stops else None


def _distance_between_zones(pair: ODPairDemand, zones: dict[str, DemandZone]) -> float:
    origin = zones.get(pair.origin_zone_id)
    destination = zones.get(pair.destination_zone_id)
    if origin is None or destination is None:
        return 0.0 if pair.origin_zone_id == pair.destination_zone_id else 1000.0
    return sqrt(
        (origin.centroid_x - destination.centroid_x) ** 2
        + (origin.centroid_y - destination.centroid_y) ** 2
    )


def _max_penalty_delta(left: dict[str, float], right: dict[str, float]) -> float:
    keys = set(left) | set(right)
    return max(
        (abs(left.get(key, 0.0) - right.get(key, 0.0)) for key in keys),
        default=0.0,
    )
