from __future__ import annotations

from dataclasses import dataclass
from math import ceil, sqrt

from .city import DemandZone
from .demand import DemandMatrix, ODPairDemand
from .choice import ChoiceConfig, alternative_probabilities, probabilities, utilities
from .network import Network
from .routing import Journey, TransitRouter
from .reference_model import (
    CROWDED_LOAD_RATIO,
    REFERENCE_MOBILITY,
    EXTREME_LOAD_RATIO,
    REFERENCE_MODE_PROFILES,
    REFERENCE_TRANSFER,
    SEVERE_LOAD_RATIO,
    crowding_time_multiplier,
    headway_unevenness_factor,
)


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

    @property
    def crowding_level(self) -> str:
        ratio = self.load_ratio
        if ratio >= EXTREME_LOAD_RATIO:
            return "extreme"
        if ratio >= SEVERE_LOAD_RATIO:
            return "severe"
        if ratio >= CROWDED_LOAD_RATIO:
            return "crowded"
        return "normal"


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
    dwell_seconds: float = 0.0
    platform_m: float = 0.0


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
    average_wait_time_min: float = 0.0
    rest_trips: float = 0.0


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
    service_headway_factors: tuple[tuple[str, float], ...] = ()


@dataclass(frozen=True, slots=True)
class AssignmentConfig:
    period_id: str
    car_speed_kph: float = 30.0
    walking_speed_kph: float = 5.0
    bike_speed_kph: float = 15.12
    choice: ChoiceConfig = ChoiceConfig()
    transfer_penalty_min: float = REFERENCE_TRANSFER.base_s / 60.0
    crowding_start_ratio: float = 0.85
    iterations: int = 6
    damping: float = 0.5
    transit_fare: float = 0.0
    convergence_tolerance: float = 1e-4
    max_access_distance_m: float = 1500.0
    max_transit_alternatives: int = 3
    alternative_diversity_penalty_min: float = 15.0

    def __post_init__(self) -> None:
        if self.car_speed_kph <= 0 or self.walking_speed_kph <= 0 or self.bike_speed_kph <= 0:
            raise ValueError("Speeds must be positive")
        if self.transfer_penalty_min < 0:
            raise ValueError("transfer_penalty_min cannot be negative")
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
        if self.max_transit_alternatives <= 0:
            raise ValueError("max_transit_alternatives must be positive")
        if self.alternative_diversity_penalty_min < 0:
            raise ValueError("alternative_diversity_penalty_min cannot be negative")


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
        zone_id: _nearest_stop_id(
            network,
            zones[zone_id],
            config.max_access_distance_m,
            config.period_id,
        )
        for zone_id in zones
    }

    segment_crowding_penalties: dict[tuple[str, str, str], float] = {}
    service_headway_factors: dict[str, float] = {}
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
            segment_crowding_penalties,
            service_headway_factors,
        )
        segment_target_penalties = _segment_crowding_penalties(
            network,
            snapshot.section_loads,
            config,
        )
        max_delta = _max_penalty_delta(
            segment_crowding_penalties,
            segment_target_penalties,
        )
        segment_crowding_penalties = {
            segment: (
                segment_crowding_penalties.get(segment, 0.0) * config.damping
                + target * (1.0 - config.damping)
            )
            for segment, target in segment_target_penalties.items()
        }
        service_target_factors = _service_headway_feedback(
            network,
            snapshot,
            config.period_id,
        )
        service_delta = _max_penalty_delta(
            service_headway_factors,
            service_target_factors,
        )
        service_headway_factors = {
            service_id: (
                service_headway_factors.get(service_id, 1.0) * config.damping
                + target * (1.0 - config.damping)
            )
            for service_id, target in service_target_factors.items()
        }
        if (
            iteration > 1
            and max(max_delta, service_delta) <= config.convergence_tolerance
        ):
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
        service_headway_factors=tuple(sorted(service_headway_factors.items())),
    )


@dataclass(frozen=True, slots=True)
class _FlowSnapshot:
    metrics: AssignmentMetrics
    route_flows: tuple[RouteFlow, ...]
    section_loads: tuple[SectionLoad, ...]
    stop_flows: tuple[StopFlow, ...]
    unserved: float
    loss_reasons: tuple[DemandLoss, ...]
    service_stop_boardings: tuple[tuple[str, str, float], ...] = ()


def _assign_once(
    network: Network,
    demand: DemandMatrix,
    router: TransitRouter,
    config: AssignmentConfig,
    zones: dict[str, DemandZone],
    zone_stops: dict[str, str | None],
    segment_crowding_penalties: dict[tuple[str, str, str], float],
    service_headway_factors: dict[str, float],
) -> _FlowSnapshot:
    section_flow: dict[tuple[str, str, str], float] = {}
    service_stop_boardings: dict[tuple[str, str], float] = {}
    section_capacity, stop_platform_m = _section_capacity_and_platforms(
        network,
        config.period_id,
    )
    route_boardings: dict[str, float] = {}
    route_traversals: dict[str, float] = {}
    stop_boardings: dict[str, float] = {}
    stop_alightings: dict[str, float] = {}
    stop_transfers: dict[str, float] = {}
    total_transit = total_car = total_walk = total_bike = total_rest = 0.0
    weighted_transit_time = weighted_transfers = weighted_wait = 0.0
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

        journeys: tuple[Journey, ...] = ()
        if origin_stop_id and destination_stop_id:
            candidates = router.shortest_alternatives(
                network.stops[origin_stop_id],
                network.stops[destination_stop_id],
                period_id=config.period_id,
                max_alternatives=config.max_transit_alternatives,
                route_penalties={},
                segment_crowding_penalties=segment_crowding_penalties,
                diversity_penalty_min=config.alternative_diversity_penalty_min,
                service_headway_factors=service_headway_factors,
            )
            journeys = tuple(
                candidate
                for candidate in candidates
                if any(leg.kind == "transit" for leg in candidate.legs)
            )

        journey = journeys[0] if journeys else None
        journey_stats = tuple(
            (
                candidate.duration_min + candidate.transfers * config.transfer_penalty_min,
                sum(leg.wait_min for leg in candidate.legs if leg.kind == "transit"),
            )
            for candidate in journeys
        )
        journey_wait = 0.0 if journey is None else journey_stats[0][1]
        transit_time = None if journey is None else journey_stats[0][0]
        no_car_share = _no_car_share(
            pair,
            zones,
            default_share=config.choice.no_car_share,
        )
        probs = probabilities(
            utilities(
                walk_time_min=walk_time,
                car_time_min=car_time,
                transit_time_min=transit_time,
                transit_wait_min=journey_wait,
                bike_time_min=bike_time,
                transit_fare=config.transit_fare,
                car_distance_km=distance_m / 1000.0,
                bike_distance_km=distance_m / 1000.0,
                config=config.choice,
                base_time_min=pair.base_time_min,
            ),
            no_car_share=no_car_share,
            bike_availability=config.choice.two_wheel_share,
        )

        transit_trips = trips * probs["transit"]
        alternative_shares = (
            alternative_probabilities(journey_stats, config=config.choice)
            if journeys else ()
        )
        car_trips = trips * probs["car"]
        walk_trips = trips * probs["walk"]
        bike_trips = trips * probs["bike"]
        rest_trips = trips * probs["rest"]
        total_transit += transit_trips
        total_car += car_trips
        total_walk += walk_trips
        total_bike += bike_trips
        total_rest += rest_trips

        if journey is None:
            unserved += transit_trips
            if transit_trips > 0:
                loss_reasons["noroute"] = loss_reasons.get("noroute", 0.0) + transit_trips
            continue

        lost_trips = max(0.0, trips - transit_trips)
        if lost_trips > 0:
            reason = _classify_demand_loss(
                transit_time=transit_time or 0.0,
                walk_time=walk_time,
                car_time=car_time,
                bike_time=bike_time,
                transfers=journey.transfers,
                wait_min=journey_wait,
                transit_fare=config.transit_fare,
                fare_weight=config.choice.transit_fare_weight,
                route_penalized=any(
                    segment_crowding_penalties.get(
                        (leg.route_id or "", leg.from_id, leg.to_id),
                        0.0,
                    ) > 0.0
                    for leg in journey.legs
                    if leg.kind == "transit"
                ),
            )
            loss_reasons[reason] = loss_reasons.get(reason, 0.0) + lost_trips

        if journeys:
            for alternative_index, (alternative_share, candidate) in enumerate(
                zip(alternative_shares, journeys)
            ):
                candidate_trips = transit_trips * alternative_share
                candidate_time, candidate_wait = journey_stats[alternative_index]
                weighted_transit_time += candidate_trips * candidate_time
                weighted_transfers += candidate_trips * candidate.transfers
                weighted_wait += candidate_trips * candidate_wait

                for index, leg in enumerate(candidate.legs):
                    if leg.kind != "transit" or leg.route_id is None:
                        continue
                    key = (leg.route_id, leg.from_id, leg.to_id)
                    section_flow[key] = section_flow.get(key, 0.0) + candidate_trips
                    if leg.service_id is not None:
                        service_key = (leg.service_id, leg.from_id)
                        service_stop_boardings[service_key] = (
                            service_stop_boardings.get(service_key, 0.0)
                            + candidate_trips
                        )
                    route_traversals[leg.route_id] = (
                        route_traversals.get(leg.route_id, 0.0) + candidate_trips
                    )

                    previous_leg = candidate.legs[index - 1] if index else None
                    next_leg = (
                        candidate.legs[index + 1]
                        if index + 1 < len(candidate.legs)
                        else None
                    )
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
                        route_boardings[leg.route_id] = (
                            route_boardings.get(leg.route_id, 0.0)
                            + candidate_trips
                        )
                        stop_boardings[leg.from_id] = (
                            stop_boardings.get(leg.from_id, 0.0)
                            + candidate_trips
                        )
                        if previous_leg is not None and previous_leg.kind == "walk":
                            stop_transfers[leg.from_id] = (
                                stop_transfers.get(leg.from_id, 0.0)
                                + candidate_trips
                            )

                    if not next_same_route:
                        stop_alightings[leg.to_id] = (
                            stop_alightings.get(leg.to_id, 0.0)
                            + candidate_trips
                        )
                        if next_leg is not None and next_leg.kind == "walk":
                            stop_transfers[leg.to_id] = (
                                stop_transfers.get(leg.to_id, 0.0)
                                + candidate_trips
                            )

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
            dwell_seconds=_stop_dwell_seconds(
                network,
                stop_id,
                config.period_id,
                stop_boardings.get(stop_id, 0.0),
            ),
            platform_m=stop_platform_m.get(stop_id, 0.0),
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
        average_wait_time_min=(
            0.0 if total_transit <= 0 else weighted_wait / total_transit
        ),
        rest_trips=total_rest,
    )
    losses = tuple(
        DemandLoss(reason, trips)
        for reason, trips in sorted(loss_reasons.items())
    )
    service_stop_rows = tuple(
        (service_id, stop_id, value)
        for (service_id, stop_id), value in sorted(service_stop_boardings.items())
    )
    return _FlowSnapshot(
        metrics,
        route_flows,
        section_loads,
        stop_flows,
        unserved,
        losses,
        service_stop_rows,
    )


def _no_car_share(
    pair: ODPairDemand,
    zones: dict[str, DemandZone],
    *,
    default_share: float,
) -> float:
    zone = zones.get(pair.origin_zone_id)
    if zone is not None:
        return zone.no_car_share
    return default_share if 0.0 <= default_share <= 1.0 else REFERENCE_MOBILITY.no_car_share

def _service_headway_feedback(
    network: Network,
    snapshot: _FlowSnapshot,
    period_id: str,
) -> dict[str, float]:
    period = network.periods[period_id]
    period_hours = (period.end_minute - period.start_minute) / 60.0
    stop_rows = {(service_id, stop_id): value for service_id, stop_id, value in snapshot.service_stop_boardings}
    factors: dict[str, float] = {}
    for service in network.services.values():
        headway = service.headway_by_period.get(period_id)
        if headway is None:
            continue
        route = network.routes[service.route_id]
        stop_boardings = tuple(
            stop_rows.get((service.id, stop_id), 0.0)
            for stop_id in route.stop_ids
        )
        factors[service.id] = headway_unevenness_factor(
            route.mode.value,
            headway,
            period_hours,
            stop_boardings,
            route_closed=route.closed,
            both_ways=route.both_ways,
        )
    return factors


def _classify_demand_loss(
    *,
    transit_time: float,
    walk_time: float,
    car_time: float,
    bike_time: float,
    transfers: int,
    wait_min: float,
    transit_fare: float,
    fare_weight: float,
    route_penalized: bool,
) -> str:
    if route_penalized:
        return "crowd"
    if wait_min > 0.5 * transit_time:
        return "wait"
    best_alternative = min(walk_time, car_time, bike_time)
    if transit_fare > 0.0 and fare_weight * transit_fare >= 0.5 * transit_time:
        return "price"
    if transfers > 0 and transit_time > best_alternative:
        return "transfer"
    if transit_time > best_alternative:
        return "ride"
    return "ride"


def _section_capacity_and_platforms(
    network: Network,
    period_id: str,
) -> tuple[tuple[tuple[str, str, str, float], ...], dict[str, float]]:
    capacities: dict[tuple[str, str, str], float] = {}
    platform_m: dict[str, float] = {}
    period = network.periods[period_id]
    duration_hours = (period.end_minute - period.start_minute) / 60.0

    grouped: dict[tuple[str, str], list[tuple[str, str, str, str, float, float, float]]] = {}
    physical_limits: dict[str, float] = {}

    for service in network.services.values():
        headway = service.headway_by_period.get(period_id)
        if headway is None:
            continue
        route = network.routes[service.route_id]
        profile = REFERENCE_MODE_PROFILES[route.mode.value]
        vehicle_capacity = network.vehicle_types[service.vehicle_type_id].capacity or profile.capacity
        scheduled_departures = (period.end_minute - period.start_minute) / headway

        for index, (from_id, to_id) in enumerate(route.segment_pairs()):
            track_id = route.track_section_for_segment(index)
            track = network.track_sections.get(track_id) if track_id else None
            if track is None:
                key = ("route", f"{route.id}:{from_id}:{to_id}")
                group_limit = profile.track_capacity_per_hour
            else:
                group_name = track.shared_group or track.id
                key = ("track", group_name)
                group_limit = min(profile.track_capacity_per_hour, track.capacity_departures_per_hour)
                physical_limits[group_name] = min(
                    physical_limits.get(group_name, float("inf")),
                    track.capacity_departures_per_hour,
                )

            # Platform length is a mode-level infrastructure requirement and
            # does not depend on an explicit TrackSection being attached.
            for stop_id in (from_id, to_id):
                platform_m[stop_id] = max(platform_m.get(stop_id, 0.0), profile.platform_m)

            grouped.setdefault(key, []).append(
                (service.id, route.id, from_id, to_id, scheduled_departures, group_limit, vehicle_capacity)
            )

    for (kind, group_name), items in grouped.items():
        if kind == "track":
            scheduled_total = sum(item[4] for item in items)
            physical_limit = physical_limits[group_name] * duration_hours
            # Track capacity describes the carrying capacity of the physical
            # corridor. Allocate it across services by their scheduled share.
            # This keeps shared corridors from double-counting departures while
            # preserving the full physical capacity for crowding analysis.
            if scheduled_total <= 0:
                continue
            group_departures = physical_limit
        else:
            group_departures = None

        scheduled_total = sum(item[4] for item in items)
        for service_id, route_id, from_id, to_id, scheduled, mode_limit, vehicle_capacity in items:
            if kind == "track":
                effective_departures = group_departures * scheduled / scheduled_total
            else:
                effective_departures = scheduled
            effective_departures = min(
                effective_departures,
                mode_limit * duration_hours,
            )
            capacity = effective_departures * vehicle_capacity
            forward_key = (route_id, from_id, to_id)
            capacities[forward_key] = capacities.get(forward_key, 0.0) + capacity
            if network.routes[route_id].both_ways:
                reverse_key = (route_id, to_id, from_id)
                capacities[reverse_key] = capacities.get(reverse_key, 0.0) + capacity

    return (
        tuple(
            (route_id, from_id, to_id, capacity)
            for (route_id, from_id, to_id), capacity in capacities.items()
        ),
        platform_m,
    )


def _segment_crowding_penalties(
    network: Network,
    sections: tuple[SectionLoad, ...],
    config: AssignmentConfig,
) -> dict[tuple[str, str, str], float]:
    penalties: dict[tuple[str, str, str], float] = {}
    for section in sections:
        if section.load_ratio <= config.crowding_start_ratio:
            continue
        route = network.routes[section.route_id]
        segment = (route.id, section.from_stop_id, section.to_stop_id)
        try:
            segment_index = route.segment_pairs().index(
                (section.from_stop_id, section.to_stop_id)
            )
        except ValueError:
            continue
        base_runtime = network.route_segment_run_time_min(route, segment_index)
        penalty = base_runtime * (
            crowding_time_multiplier(section.load_ratio) - 1.0
        )
        penalties[segment] = penalties.get(segment, 0.0) + penalty
    return penalties


def _stop_dwell_seconds(
    network: Network,
    stop_id: str,
    period_id: str,
    boardings: float,
) -> float:
    total = 0.0
    for service in network.services.values():
        headway = service.headway_by_period.get(period_id)
        if headway is None:
            continue
        route = network.routes[service.route_id]
        if stop_id not in route.stop_ids:
            continue
        profile = REFERENCE_MODE_PROFILES[route.mode.value]
        departures = network.service_departures(service, period_id)
        total += departures * profile.dwell_s
        total += boardings * profile.dwell_per_passenger_s
    return total


def _nearest_stop_id(
    network: Network,
    zone: DemandZone,
    max_distance_m: float,
    period_id: str,
) -> str | None:
    best_id: str | None = None
    best_distance = max_distance_m

    stop_access_limits: dict[str, float] = {}
    for service in network.services.values():
        if service.headway_by_period.get(period_id) is None:
            continue
        route = network.routes[service.route_id]
        profile = REFERENCE_MODE_PROFILES[route.mode.value]
        for stop_id in route.stop_ids:
            stop_access_limits[stop_id] = max(
                stop_access_limits.get(stop_id, 0.0),
                profile.access_m,
            )

    for stop in network.stops.values():
        mode_access = stop_access_limits.get(stop.id, 0.0)
        allowed_distance = min(max_distance_m, mode_access)
        if allowed_distance <= 0.0:
            continue
        distance = sqrt(
            (stop.location.x - zone.centroid_x) ** 2
            + (stop.location.y - zone.centroid_y) ** 2
        )
        if distance <= allowed_distance and distance <= best_distance:
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
