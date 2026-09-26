from __future__ import annotations

from dataclasses import dataclass
from math import ceil, sqrt

from .assignment import AssignmentResult
from .network import Network, TransitMode


@dataclass(frozen=True, slots=True)
class EconomicsConfig:
    period_id: str
    fare_per_transit_trip: float = 0.0
    annual_days: int = 365
    infrastructure_cost_per_km: dict[TransitMode, float] | None = None
    station_cost: float = 0.0

    def __post_init__(self) -> None:
        if self.fare_per_transit_trip < 0:
            raise ValueError("fare_per_transit_trip cannot be negative")
        if self.annual_days <= 0:
            raise ValueError("annual_days must be positive")
        if self.station_cost < 0:
            raise ValueError("station_cost cannot be negative")


@dataclass(frozen=True, slots=True)
class EconomicsResult:
    daily_vehicle_km: float
    daily_operating_cost: float
    daily_fare_revenue: float
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
    daily_operating_cost = 0.0
    capital_cost = 0.0
    infrastructure_costs = config.infrastructure_cost_per_km or {}

    for service in network.services.values():
        headway = service.headway_by_period.get(config.period_id)
        if headway is None:
            continue
        departures = ceil(duration / headway)
        route = network.routes[service.route_id]
        length_km = _route_length_km(network, route.stop_ids)
        vehicle = network.vehicle_types[service.vehicle_type_id]

        # Service is represented per direction in this engine slice.
        vehicle_km = departures * length_km * 2.0
        daily_vehicle_km += vehicle_km
        daily_operating_cost += vehicle_km * vehicle.operating_cost_per_km
        capital_cost += length_km * infrastructure_costs.get(route.mode, 0.0)
        capital_cost += len(route.stop_ids) * config.station_cost

    transit_trips = assignment.metrics.transit_trips
    daily_fare_revenue = transit_trips * config.fare_per_transit_trip
    return EconomicsResult(
        daily_vehicle_km=daily_vehicle_km,
        daily_operating_cost=daily_operating_cost,
        daily_fare_revenue=daily_fare_revenue,
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


def _route_length_km(network: Network, stop_ids: tuple[str, ...]) -> float:
    total_m = 0.0
    for left_id, right_id in zip(stop_ids, stop_ids[1:]):
        left = network.stops[left_id]
        right = network.stops[right_id]
        total_m += sqrt(
            (left.location.x - right.location.x) ** 2
            + (left.location.y - right.location.y) ** 2
        )
    return total_m / 1000.0
