from __future__ import annotations

from dataclasses import dataclass
from math import ceil

from .assignment import AssignmentResult
from .infrastructure import TrackType
from .network import Network, TransitMode
from .reference_model import REFERENCE_MODE_PROFILES


@dataclass(frozen=True, slots=True)
class EconomicsConfig:
    period_id: str
    fare_per_transit_trip: float = 0.0
    annual_days: int = 365
    infrastructure_cost_per_km: dict[TransitMode, float] | None = None
    infrastructure_cost_per_track_km: dict[TrackType, float] | None = None
    station_cost: float = 0.0

    def __post_init__(self) -> None:
        if self.fare_per_transit_trip < 0:
            raise ValueError("fare_per_transit_trip cannot be negative")
        if self.annual_days <= 0:
            raise ValueError("annual_days must be positive")
        if self.station_cost < 0:
            raise ValueError("station_cost cannot be negative")
        if self.infrastructure_cost_per_km is not None and any(
            value < 0 for value in self.infrastructure_cost_per_km.values()
        ):
            raise ValueError("Infrastructure costs cannot be negative")
        if self.infrastructure_cost_per_track_km is not None and any(
            value < 0 for value in self.infrastructure_cost_per_track_km.values()
        ):
            raise ValueError("Track infrastructure costs cannot be negative")


@dataclass(frozen=True, slots=True)
class EconomicsResult:
    daily_vehicle_km: float
    daily_fleet_cost: float
    daily_operating_cost: float
    daily_fare_revenue: float
    annual_fleet_cost: float
    annual_operating_cost: float
    annual_fare_revenue: float
    capital_cost: float
    operating_cost_per_transit_trip: float
    revenue_per_transit_trip: float


def calculate_economics(
    network: Network,
    assignment: AssignmentResult,
    *,
    config: EconomicsConfig,
) -> EconomicsResult:
    period = network.periods[config.period_id]
    duration = period.end_minute - period.start_minute
    daily_vehicle_km = 0.0
    daily_fleet_cost = 0.0
    daily_operating_cost = 0.0
    capital_cost = 0.0
    infrastructure_costs = config.infrastructure_cost_per_km or {}
    track_infrastructure_costs = config.infrastructure_cost_per_track_km or {}

    active_routes: set[str] = set()
    for service in network.services.values():
        headway = service.headway_by_period.get(config.period_id)
        if headway is None:
            continue
        departures = ceil(duration / headway)
        route = network.routes[service.route_id]
        active_routes.add(route.id)
        length_km = network.route_length_km(route)
        vehicle = network.vehicle_types[service.vehicle_type_id]
        profile = REFERENCE_MODE_PROFILES[route.mode.value]
        direction_factor = 1.0 if not route.both_ways else 2.0
        cycle_run_min = direction_factor * network.route_run_time_min(route)
        cycle_dwell_min = 2.0 * len(route.stop_ids) * profile.dwell_s / 60.0
        cycle_turnback_min = 0.0 if route.closed else 2.0 * profile.turnback_s / 60.0
        cycle_time_min = cycle_run_min + cycle_dwell_min + cycle_turnback_min
        required_vehicles = max(1, ceil(cycle_time_min / headway))
        daily_fleet_cost += required_vehicles * profile.vehicle_cost_day

        # Service is represented per direction in this engine slice.
        vehicle_km = departures * length_km * direction_factor
        daily_vehicle_km += vehicle_km
        operating_cost_per_km = vehicle.operating_cost_per_km or profile.opex_per_vehicle_km
        daily_operating_cost += vehicle_km * operating_cost_per_km
    for route_id in active_routes:
        route = network.routes[route_id]
        length_km = _route_length_km(network, route)
        capital_cost += _route_capital_cost(
            network,
            route,
            length_km=length_km,
            mode_costs=infrastructure_costs,
            track_costs=track_infrastructure_costs,
        )
        capital_cost += sum(
            config.station_cost
            for stop_id in route.stop_ids
            if network.stops[stop_id].is_station
        )

    transit_trips = assignment.metrics.transit_trips
    daily_fare_revenue = transit_trips * config.fare_per_transit_trip
    return EconomicsResult(
        daily_vehicle_km=daily_vehicle_km,
        daily_fleet_cost=daily_fleet_cost,
        daily_operating_cost=daily_operating_cost,
        daily_fare_revenue=daily_fare_revenue,
        annual_fleet_cost=daily_fleet_cost * config.annual_days,
        annual_operating_cost=daily_operating_cost * config.annual_days,
        annual_fare_revenue=daily_fare_revenue * config.annual_days,
        capital_cost=capital_cost,
        operating_cost_per_transit_trip=(
            0.0 if transit_trips <= 0 else daily_operating_cost / transit_trips
        ),
        revenue_per_transit_trip=(
            0.0 if transit_trips <= 0 else daily_fare_revenue / transit_trips
        ),
    )


def _route_capital_cost(
    network: Network,
    route,
    *,
    length_km: float,
    mode_costs: dict[TransitMode, float],
    track_costs: dict[TrackType, float],
) -> float:
    if route.track_section_ids and track_costs:
        return sum(
            network.track_sections[section_id].length_km
            * track_costs.get(network.track_sections[section_id].track_type, 0.0)
            for section_id in route.track_section_ids
        )
    return length_km * mode_costs.get(route.mode, 0.0)
