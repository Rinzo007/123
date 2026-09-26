from __future__ import annotations

from dataclasses import dataclass
from .demand import DemandMatrix
from .network import Network


@dataclass(frozen=True, slots=True)
class ServiceCapacity:
    service_id: str
    period_id: str
    departures: int
    vehicle_capacity: int
    offered_capacity: int


@dataclass(frozen=True, slots=True)
class SimulationResult:
    total_demand_trips_per_day: float
    service_capacities: tuple[ServiceCapacity, ...]

    @property
    def total_transit_supply(self) -> int:
        return sum(item.offered_capacity for item in self.service_capacities)


def calculate_service_capacity(
    network: Network,
    service_id: str,
    period_id: str,
) -> ServiceCapacity:
    service = network.services[service_id]
    period = network.periods[period_id]
    vehicle = network.vehicle_types[service.vehicle_type_id]
    headway = service.headway_by_period[period_id]
    departures = network.service_departures(service, period_id)
    return ServiceCapacity(
        service_id=service.id,
        period_id=period.id,
        departures=departures,
        vehicle_capacity=vehicle.capacity,
        offered_capacity=departures * vehicle.capacity,
    )


def simulate_capacity(network: Network, demand: DemandMatrix) -> SimulationResult:
    capacities = tuple(
        calculate_service_capacity(network, service.id, period.id)
        for service in network.services.values()
        for period in network.periods.values()
        if period.id in service.headway_by_period
    )
    return SimulationResult(demand.total_trips_per_day, capacities)
