from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from .geo import LineString, Point
from .reference_model import REFERENCE_PERIODS


class TransitMode(StrEnum):
    BUS = "bus"
    TRAM = "tram"
    METRO = "metro"
    RAIL = "rail"


@dataclass(frozen=True, slots=True)
class VehicleType:
    id: str
    name: str
    mode: TransitMode
    capacity: int
    operating_cost_per_km: float = 0.0

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("Vehicle type id cannot be empty")
        if self.capacity <= 0:
            raise ValueError("Vehicle capacity must be positive")
        if self.operating_cost_per_km < 0:
            raise ValueError("Operating cost cannot be negative")


@dataclass(frozen=True, slots=True)
class Stop:
    id: str
    name: str
    location: Point
    is_station: bool = False


@dataclass(frozen=True, slots=True)
class Route:
    id: str
    name: str
    mode: TransitMode
    stop_ids: tuple[str, ...]
    geometry: LineString | None = None

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("Route id cannot be empty")
        if len(self.stop_ids) < 2:
            raise ValueError("A route needs at least two stops")


@dataclass(frozen=True, slots=True)
class ServicePeriod:
    id: str
    start_minute: int
    end_minute: int

    def __post_init__(self) -> None:
        if not (0 <= self.start_minute < self.end_minute <= 1440):
            raise ValueError("Invalid service period")


@dataclass(frozen=True, slots=True)
class Service:
    id: str
    route_id: str
    vehicle_type_id: str
    headway_by_period: dict[str, float]
    departure_offset_by_period: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("Service id cannot be empty")
        if any(headway <= 0 for headway in self.headway_by_period.values()):
            raise ValueError("Headways must be positive")
        if any(offset < 0 or offset >= 1440 for offset in self.departure_offset_by_period.values()):
            raise ValueError("Departure offsets must be within the day")


@dataclass(slots=True)
class Network:
    stops: dict[str, Stop] = field(default_factory=dict)
    routes: dict[str, Route] = field(default_factory=dict)
    vehicle_types: dict[str, VehicleType] = field(default_factory=dict)
    periods: dict[str, ServicePeriod] = field(default_factory=dict)
    services: dict[str, Service] = field(default_factory=dict)

    def add_stop(self, stop: Stop) -> None:
        self._add_unique(self.stops, stop.id, "stop")
        self.stops[stop.id] = stop

    def add_route(self, route: Route) -> None:
        self._add_unique(self.routes, route.id, "route")
        missing = [sid for sid in route.stop_ids if sid not in self.stops]
        if missing:
            raise ValueError(f"Route {route.id} references unknown stops: {missing}")
        self.routes[route.id] = route

    def add_vehicle_type(self, vehicle: VehicleType) -> None:
        self._add_unique(self.vehicle_types, vehicle.id, "vehicle type")
        self.vehicle_types[vehicle.id] = vehicle

    def add_period(self, period: ServicePeriod) -> None:
        self._add_unique(self.periods, period.id, "service period")
        self.periods[period.id] = period

    def add_service(self, service: Service) -> None:
        self._add_unique(self.services, service.id, "service")
        if service.route_id not in self.routes:
            raise ValueError(f"Unknown route: {service.route_id}")
        if service.vehicle_type_id not in self.vehicle_types:
            raise ValueError(f"Unknown vehicle type: {service.vehicle_type_id}")
        missing = [pid for pid in service.headway_by_period if pid not in self.periods]
        if missing:
            raise ValueError(f"Service {service.id} references unknown periods: {missing}")
        unknown_offsets = [pid for pid in service.departure_offset_by_period if pid not in self.periods]
        if unknown_offsets:
            raise ValueError(f"Service {service.id} references unknown offset periods: {unknown_offsets}")
        route = self.routes[service.route_id]
        vehicle = self.vehicle_types[service.vehicle_type_id]
        if route.mode != vehicle.mode:
            raise ValueError(
                f"Service {service.id} uses vehicle mode {vehicle.mode} for route mode {route.mode}"
            )
        self.services[service.id] = service

    def validate(self) -> list[str]:
        errors: list[str] = []
        for route in self.routes.values():
            if len(route.stop_ids) != len(set(route.stop_ids)):
                errors.append(f"Route {route.id} contains duplicate stops")
        return errors

    @staticmethod
    def _add_unique(collection: dict[str, object], item_id: str, kind: str) -> None:
        if not item_id.strip():
            raise ValueError(f"{kind.title()} id cannot be empty")
        if item_id in collection:
            raise ValueError(f"Duplicate {kind} id: {item_id}")


def default_service_periods() -> tuple[ServicePeriod, ...]:
    """Return the canonical five operating periods used by the model."""
    return tuple(
        ServicePeriod(period.key, period.start_minute, period.end_minute)
        for period in REFERENCE_PERIODS
    )
