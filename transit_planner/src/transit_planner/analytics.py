from __future__ import annotations

from dataclasses import dataclass
from math import ceil, sqrt

from .assignment import AssignmentResult
from .city import DemandZone
from .network import Network
from .reference_model import REFERENCE_MODE_PROFILES


@dataclass(frozen=True, slots=True)
class StopAnalytics:
    stop_id: str
    name: str
    boardings: float
    alightings: float
    transfers: float
    dwell_seconds: float = 0.0
    platform_m: float = 0.0

    @property
    def total_activity(self) -> float:
        return self.boardings + self.alightings


@dataclass(frozen=True, slots=True)
class SectionAnalytics:
    route_id: str
    from_stop_id: str
    to_stop_id: str
    distance_km: float
    passengers: float
    capacity: float
    load_ratio: float

    @property
    def crowding_level(self) -> str:
        if self.load_ratio >= 4.0:
            return "extreme"
        if self.load_ratio >= 2.0:
            return "severe"
        if self.load_ratio >= 1.0:
            return "crowded"
        return "normal"


@dataclass(frozen=True, slots=True)
class AccessibilityResult:
    radius_m: float
    population_total: float
    population_covered: float
    jobs_total: float
    jobs_covered: float

    @property
    def population_share(self) -> float:
        return _share(self.population_covered, self.population_total)

    @property
    def jobs_share(self) -> float:
        return _share(self.jobs_covered, self.jobs_total)


@dataclass(frozen=True, slots=True)
class ServiceAnalytics:
    service_id: str
    route_id: str
    period_id: str
    departures: int
    fleet: int
    daily_vehicle_km: float
    daily_opex: float
    capacity_per_direction: float


@dataclass(frozen=True, slots=True)
class NetworkAnalytics:
    transit_share: float
    average_transit_time_min: float
    average_transfers: float
    max_load_ratio: float
    overloaded_sections: int
    passenger_km: float
    stops: tuple[StopAnalytics, ...]
    sections: tuple[SectionAnalytics, ...]
    accessibility: tuple[AccessibilityResult, ...]
    services: tuple[ServiceAnalytics, ...] = ()
    severe_sections: int = 0
    extreme_sections: int = 0


def analyze_network(
    network: Network,
    assignment: AssignmentResult,
    *,
    zones: tuple[DemandZone, ...] = (),
    accessibility_radii_m: tuple[float, ...] = (500.0, 1000.0),
) -> NetworkAnalytics:
    stops = tuple(
        StopAnalytics(
            stop_id=item.stop_id,
            name=network.stops[item.stop_id].name,
            boardings=item.boardings,
            alightings=item.alightings,
            transfers=item.transfers,
            dwell_seconds=item.dwell_seconds,
            platform_m=item.platform_m,
        )
        for item in assignment.stop_flows
    )

    sections: list[SectionAnalytics] = []
    passenger_km = 0.0
    for item in assignment.section_loads:
        distance_km = _stop_distance_km(
            network.stops[item.from_stop_id],
            network.stops[item.to_stop_id],
        )
        passenger_km += item.passengers * distance_km
        sections.append(
            SectionAnalytics(
                route_id=item.route_id,
                from_stop_id=item.from_stop_id,
                to_stop_id=item.to_stop_id,
                distance_km=distance_km,
                passengers=item.passengers,
                capacity=item.capacity,
                load_ratio=item.load_ratio,
            )
        )

    services = tuple(
        _service_analytics(network, service_id, period_id)
        for service_id, service in network.services.items()
        for period_id in service.headway_by_period
    )
    accessibility = tuple(
        calculate_accessibility(network, zones, radius_m=radius)
        for radius in accessibility_radii_m
    )
    return NetworkAnalytics(
        transit_share=assignment.metrics.transit_share,
        average_transit_time_min=assignment.metrics.average_transit_time_min,
        average_transfers=assignment.metrics.average_transfers,
        max_load_ratio=assignment.max_load_ratio,
        overloaded_sections=sum(item.load_ratio >= 1.0 for item in sections),
        severe_sections=sum(item.load_ratio >= 2.0 for item in sections),
        extreme_sections=sum(item.load_ratio >= 4.0 for item in sections),
        passenger_km=passenger_km,
        stops=stops,
        sections=tuple(sections),
        accessibility=accessibility,
        services=services,
    )


def _service_analytics(
    network: Network,
    service_id: str,
    period_id: str,
) -> ServiceAnalytics:
    service = network.services[service_id]
    route = network.routes[service.route_id]
    period = network.periods[period_id]
    profile = REFERENCE_MODE_PROFILES[route.mode.value]
    headway = service.headway_by_period[period_id]
    duration_min = period.end_minute - period.start_minute
    departures = ceil(duration_min / headway)
    length_km = sum(
        _stop_distance_km(
            network.stops[left_id],
            network.stops[right_id],
        )
        for left_id, right_id in zip(route.stop_ids, route.stop_ids[1:])
    )
    speed_kph = profile.rows[profile.default_row].speed_kph
    run_min = 2.0 * length_km / speed_kph * 60.0
    dwell_min = 2.0 * len(route.stop_ids) * profile.dwell_s / 60.0
    turnback_min = 2.0 * profile.turnback_s / 60.0
    cycle_min = run_min + dwell_min + turnback_min
    fleet = max(1, ceil(cycle_min / headway))
    vehicle_km = departures * length_km * 2.0
    vehicle = network.vehicle_types[service.vehicle_type_id]
    opex = vehicle_km * (vehicle.operating_cost_per_km or profile.opex_per_vehicle_km)
    return ServiceAnalytics(
        service_id=service_id,
        route_id=route.id,
        period_id=period_id,
        departures=departures,
        fleet=fleet,
        daily_vehicle_km=vehicle_km,
        daily_opex=opex,
        capacity_per_direction=departures * (vehicle.capacity or profile.capacity),
    )


def calculate_accessibility(
    network: Network,
    zones: tuple[DemandZone, ...],
    *,
    radius_m: float,
) -> AccessibilityResult:
    if radius_m < 0:
        raise ValueError("radius_m cannot be negative")

    population_total = sum(max(0.0, zone.population) for zone in zones)
    jobs_total = sum(max(0.0, zone.jobs) for zone in zones)
    population_covered = 0.0
    jobs_covered = 0.0

    for zone in zones:
        covered = any(
            sqrt(
                (stop.location.x - zone.centroid_x) ** 2
                + (stop.location.y - zone.centroid_y) ** 2
            ) <= radius_m
            for stop in network.stops.values()
        )
        if covered:
            population_covered += max(0.0, zone.population)
            jobs_covered += max(0.0, zone.jobs)

    return AccessibilityResult(
        radius_m=radius_m,
        population_total=population_total,
        population_covered=population_covered,
        jobs_total=jobs_total,
        jobs_covered=jobs_covered,
    )


def _stop_distance_km(left, right) -> float:
    return sqrt(
        (left.location.x - right.location.x) ** 2
        + (left.location.y - right.location.y) ** 2
    ) / 1000.0


def _share(numerator: float, denominator: float) -> float:
    return 0.0 if denominator <= 0 else numerator / denominator
