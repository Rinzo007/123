from __future__ import annotations

from dataclasses import dataclass
from math import ceil

from .assignment import AssignmentResult
from .network import Network


@dataclass(frozen=True, slots=True)
class EconomicsConfig:
    period_id: str
    fare_per_transit_trip: float = 0.0
    annual_days: int = 365


    def __post_init__(self) -> None:
        if self.fare_per_transit_trip < 0:
            raise ValueError("fare_per_transit_trip cannot be negative")
        if self.annual_days <= 0:
            raise ValueError("annual_days must be positive")


@dataclass(frozen=True, slots=True)
class EconomicsResult:
    daily_vehicle_km: float
    daily_fleet_cost: float
    daily_operating_cost: float
    daily_fare_revenue: float
    annual_fleet_cost: float
    annual_operating_cost: float
    annual_fare_revenue: float
    operating_cost_per_transit_trip: float
    revenue_per_transit_trip: float


def calculate_economics(
    network: Network,
    assignment: AssignmentResult,
    *,
    config: EconomicsConfig,
) -> EconomicsResult:
    daily_vehicle_km = 0.0
    daily_fleet_cost = 0.0
    daily_operating_cost = 0.0

    for service in network.services.values():
        headway = service.headway_by_period.get(config.period_id)
        if headway is None:
            continue
        departures = network.service_departures(service, config.period_id)
        route = network.routes[service.route_id]
        length_km = network.route_length_km(route)
        vehicle = network.vehicle_types[service.vehicle_type_id]
        required_vehicles = max(1, ceil(network.route_cycle_time_min(route) / headway))
        stock = next((item for item in network.rolling_stock.values() if item.vehicle_type_id == vehicle.id), None)
        daily_fleet_cost += required_vehicles * (stock.car_cost if stock is not None else 0.0)

        # Service is represented per direction in this engine slice.
        direction_factor = 1.0 if not route.both_ways else 2.0
        vehicle_km = departures * length_km * direction_factor
        daily_vehicle_km += vehicle_km
        operating_cost_per_km = vehicle.operating_cost_per_km
        daily_operating_cost += vehicle_km * operating_cost_per_km

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
            operating_cost_per_transit_trip=0.0,
            revenue_per_transit_trip=0.0,
        )

    daily_vehicle_km = sum(item.daily_vehicle_km for item in period_results)
    daily_operating_cost = sum(item.daily_operating_cost for item in period_results)
    daily_fare_revenue = sum(item.daily_fare_revenue for item in period_results)
    daily_fleet_cost = max(item.daily_fleet_cost for item in period_results)
    transit_trips = temporal_assignment.total_transit_trips
    return EconomicsResult(
        daily_vehicle_km=daily_vehicle_km,
        daily_fleet_cost=daily_fleet_cost,
        daily_operating_cost=daily_operating_cost,
        daily_fare_revenue=daily_fare_revenue,
        annual_fleet_cost=daily_fleet_cost * config.annual_days,
        annual_operating_cost=daily_operating_cost * config.annual_days,
        annual_fare_revenue=daily_fare_revenue * config.annual_days,
        operating_cost_per_transit_trip=(
            0.0 if transit_trips <= 0 else daily_operating_cost / transit_trips
        ),
        revenue_per_transit_trip=(
            0.0 if transit_trips <= 0 else daily_fare_revenue / transit_trips
        ),
    )


