from __future__ import annotations

from dataclasses import dataclass
from math import ceil

from .assignment import AssignmentResult
from .infrastructure import TrackType
from .reference_model import TrackRow, REFERENCE_MODE_PROFILES
from .network import Network, TransitMode


@dataclass(frozen=True, slots=True)
class EconomicsConfig:
    period_id: str
    fare_per_transit_trip: float = 0.0
    annual_days: int = 365
    infrastructure_cost_per_km: dict[TransitMode, float] | None = None
    infrastructure_cost_per_track_km: dict[TrackType, float] | None = None
    station_cost: float = 0.0
    reference_cost_multiplier: float = 1.0
    reference_row_cost_multipliers: dict[TrackRow, float] | None = None
    reference_segment_cost_multipliers: dict[str, tuple[float, ...]] | None = None

    def __post_init__(self) -> None:
        if self.fare_per_transit_trip < 0:
            raise ValueError("fare_per_transit_trip cannot be negative")
        if self.annual_days <= 0:
            raise ValueError("annual_days must be positive")
        if self.station_cost < 0:
            raise ValueError("station_cost cannot be negative")
        if self.reference_cost_multiplier < 0:
            raise ValueError("reference_cost_multiplier cannot be negative")
        if self.reference_row_cost_multipliers is not None and any(
            value < 0 for value in self.reference_row_cost_multipliers.values()
        ):
            raise ValueError("Reference row cost multipliers cannot be negative")
        if self.reference_segment_cost_multipliers is not None and any(
            value < 0 for values in self.reference_segment_cost_multipliers.values()
            for value in values
        ):
            raise ValueError("Reference segment cost multipliers cannot be negative")
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
        departures = network.service_departures(service, config.period_id)
        route = network.routes[service.route_id]
        active_routes.add(route.id)
        length_km = network.route_length_km(route)
        vehicle = network.vehicle_types[service.vehicle_type_id]
        profile = REFERENCE_MODE_PROFILES[route.mode.value]
        required_vehicles = max(1, ceil(network.route_cycle_time_min(route) / headway))
        daily_fleet_cost += required_vehicles * profile.vehicle_cost_day

        # Service is represented per direction in this engine slice.
        direction_factor = 1.0 if not route.both_ways else 2.0
        vehicle_km = departures * length_km * direction_factor
        daily_vehicle_km += vehicle_km
        operating_cost_per_km = vehicle.operating_cost_per_km or profile.opex_per_vehicle_km
        daily_operating_cost += vehicle_km * operating_cost_per_km
    for route_id in active_routes:
        route = network.routes[route_id]
        length_km = network.route_length_km(route)
        capital_cost += _route_capital_cost(
            network,
            route,
            length_km=length_km,
            mode_costs=infrastructure_costs,
            track_costs=track_infrastructure_costs,
            reference_cost_multiplier=config.reference_cost_multiplier,
            reference_row_cost_multipliers=config.reference_row_cost_multipliers,
            reference_segment_cost_multipliers=config.reference_segment_cost_multipliers,
        )
        physical_station_ids = {
            station_id
            for section_id in route.track_section_ids
            for station_id in network.track_sections[section_id].station_ids
        }
        if physical_station_ids:
            capital_cost += len(physical_station_ids) * config.station_cost
        else:
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


def calculate_temporal_economics(
    network: Network,
    temporal_assignment,
    *,
    config: EconomicsConfig,
) -> tuple[EconomicsResult, ...]:
    """Рассчитывает экономику каждого периода поверх временного назначения."""
    results: list[EconomicsResult] = []
    for period in temporal_assignment.periods:
        period_config = EconomicsConfig(
            period_id=period.period_id,
            fare_per_transit_trip=config.fare_per_transit_trip,
            annual_days=config.annual_days,
            infrastructure_cost_per_km=config.infrastructure_cost_per_km,
            infrastructure_cost_per_track_km=config.infrastructure_cost_per_track_km,
            station_cost=config.station_cost,
            reference_cost_multiplier=config.reference_cost_multiplier,
            reference_row_cost_multipliers=config.reference_row_cost_multipliers,
            reference_segment_cost_multipliers=config.reference_segment_cost_multipliers,
        )
        results.append(
            calculate_economics(
                network,
                period.result,
                config=period_config,
            )
        )
    return tuple(results)


def aggregate_temporal_economics(
    network: Network,
    temporal_assignment,
    *,
    config: EconomicsConfig,
) -> EconomicsResult:
    """Сводит периодическую экономику в один дневной отчёт."""
    period_results = calculate_temporal_economics(
        network,
        temporal_assignment,
        config=config,
    )
    if not period_results:
        return EconomicsResult(
            daily_vehicle_km=0.0,
            daily_fleet_cost=0.0,
            daily_operating_cost=0.0,
            daily_fare_revenue=0.0,
            annual_fleet_cost=0.0,
            annual_operating_cost=0.0,
            annual_fare_revenue=0.0,
            capital_cost=0.0,
            operating_cost_per_transit_trip=0.0,
            revenue_per_transit_trip=0.0,
        )

    daily_vehicle_km = sum(item.daily_vehicle_km for item in period_results)
    daily_operating_cost = sum(item.daily_operating_cost for item in period_results)
    daily_fare_revenue = sum(item.daily_fare_revenue for item in period_results)
    daily_fleet_cost = max(item.daily_fleet_cost for item in period_results)
    capital_cost = _network_capital_cost(
        network,
        infrastructure_costs=config.infrastructure_cost_per_km or {},
        track_infrastructure_costs=config.infrastructure_cost_per_track_km or {},
        station_cost=config.station_cost,
        reference_cost_multiplier=config.reference_cost_multiplier,
        reference_row_cost_multipliers=config.reference_row_cost_multipliers,
        reference_segment_cost_multipliers=config.reference_segment_cost_multipliers,
    )
    transit_trips = temporal_assignment.total_transit_trips
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


def _network_capital_cost(
    network: Network,
    *,
    infrastructure_costs: dict[TransitMode, float],
    track_infrastructure_costs: dict[TrackType, float],
    station_cost: float,
    reference_cost_multiplier: float,
    reference_row_cost_multipliers: dict[TrackRow, float] | None,
    reference_segment_cost_multipliers: dict[str, tuple[float, ...]] | None = None,
) -> float:
    active_routes = {
        service.route_id
        for service in network.services.values()
        if service.headway_by_period
    }
    total = 0.0
    for route_id in active_routes:
        route = network.routes[route_id]
        length_km = network.route_length_km(route)
        total += _route_capital_cost(
            network,
            route,
            length_km=length_km,
            mode_costs=infrastructure_costs,
            track_costs=track_infrastructure_costs,
            reference_cost_multiplier=reference_cost_multiplier,
            reference_row_cost_multipliers=reference_row_cost_multipliers,
        )
        physical_station_ids = {
            station_id
            for section_id in route.track_section_ids
            for station_id in network.track_sections[section_id].station_ids
        }
        if physical_station_ids:
            total += len(physical_station_ids) * station_cost
        else:
            total += sum(
                station_cost
                for stop_id in route.stop_ids
                if network.stops[stop_id].is_station
            )
    return total


def _route_capital_cost(
    network: Network,
    route,
    *,
    length_km: float,
    mode_costs: dict[TransitMode, float],
    track_costs: dict[TrackType, float],
    reference_cost_multiplier: float = 1.0,
    reference_row_cost_multipliers: dict[TrackRow, float] | None = None,
    reference_segment_cost_multipliers: dict[str, tuple[float, ...]] | None = None,
) -> float:
    if route.track_section_ids and track_costs:
        return sum(
            network.track_sections[section_id].length_km
            * track_costs.get(network.track_sections[section_id].track_type, 0.0)
            for section_id in route.track_section_ids
        )
    if route.mode in mode_costs:
        return length_km * mode_costs[route.mode]

    row_multipliers = reference_row_cost_multipliers or {}
    route_multipliers = (reference_segment_cost_multipliers or {}).get(route.id, ())
    return sum(
        network.route_segment_length_km(route, index)
        * network.route_segment_cost_per_km(route, index)
        * reference_cost_multiplier
        * row_multipliers.get(network.route_segment_row(route, index), 1.0)
        * (route_multipliers[index] if index < len(route_multipliers) else 1.0)
        for index in range(len(route.segment_pairs()))
    )
