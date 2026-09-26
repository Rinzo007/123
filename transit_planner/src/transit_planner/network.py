from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from math import ceil, hypot

from .geo import LineString, Point
from .infrastructure import TrackSection
from .reference_model import REFERENCE_MODE_PROFILES, REFERENCE_PERIODS


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
    track_section_ids: tuple[str, ...] = ()
    both_ways: bool = True
    closed: bool = False

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("Route id cannot be empty")
        if len(self.stop_ids) < 2:
            raise ValueError("A route needs at least two stops")
        expected_segments = len(self.stop_ids) if self.closed else len(self.stop_ids) - 1
        if self.track_section_ids and len(self.track_section_ids) != expected_segments:
            raise ValueError("track_section_ids must match route segments")

    def segment_pairs(self) -> tuple[tuple[str, str], ...]:
        pairs = list(zip(self.stop_ids, self.stop_ids[1:]))
        if self.closed:
            pairs.append((self.stop_ids[-1], self.stop_ids[0]))
        return tuple(pairs)

    def track_section_for_segment(self, index: int) -> str | None:
        if index < 0 or index >= len(self.segment_pairs()):
            raise IndexError("segment index out of range")
        if not self.track_section_ids:
            return None
        return self.track_section_ids[index]


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
    track_sections: dict[str, TrackSection] = field(default_factory=dict)

    def add_stop(self, stop: Stop) -> None:
        self._add_unique(self.stops, stop.id, "stop")
        self.stops[stop.id] = stop

    def add_route(self, route: Route) -> None:
        self._add_unique(self.routes, route.id, "route")
        missing = [sid for sid in route.stop_ids if sid not in self.stops]
        if missing:
            raise ValueError(f"Route {route.id} references unknown stops: {missing}")
        missing_tracks = [sid for sid in route.track_section_ids if sid not in self.track_sections]
        if missing_tracks:
            raise ValueError(f"Route {route.id} references unknown track sections: {missing_tracks}")
        self.routes[route.id] = route

    def add_vehicle_type(self, vehicle: VehicleType) -> None:
        self._add_unique(self.vehicle_types, vehicle.id, "vehicle type")
        self.vehicle_types[vehicle.id] = vehicle

    def add_period(self, period: ServicePeriod) -> None:
        self._add_unique(self.periods, period.id, "service period")
        self.periods[period.id] = period

    def add_track_section(self, section: TrackSection) -> None:
        self._add_unique(self.track_sections, section.id, "track section")
        self.track_sections[section.id] = section

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

    def route_segment_length_km(self, route: Route, index: int) -> float:
        left_id, right_id = route.segment_pairs()[index]
        section_id = route.track_section_for_segment(index)
        if section_id is not None:
            return self.track_sections[section_id].length_km
        return _point_distance_km(
            self.stops[left_id].location,
            self.stops[right_id].location,
        )

    def route_segment_run_time_min(self, route: Route, index: int) -> float:
        profile = REFERENCE_MODE_PROFILES[route.mode.value]
        section_id = route.track_section_for_segment(index)
        speed = profile.rows[profile.default_row].speed_kph
        if section_id is not None:
            section = self.track_sections[section_id]
            if section.speed_limit_kph is not None:
                speed = section.speed_limit_kph
        return self.route_segment_length_km(route, index) / speed * 60.0

    def route_length_km(self, route: Route) -> float:
        return sum(
            self.route_segment_length_km(route, index)
            for index in range(len(route.segment_pairs()))
        )

    def route_run_time_min(self, route: Route) -> float:
        return sum(
            self.route_segment_run_time_min(route, index)
            for index in range(len(route.segment_pairs()))
        )

    def route_cycle_time_min(self, route: Route) -> float:
        profile = REFERENCE_MODE_PROFILES[route.mode.value]
        direction_factor = 1.0 if not route.both_ways else 2.0
        run_min = direction_factor * self.route_run_time_min(route)
        dwell_min = 2.0 * len(route.stop_ids) * profile.dwell_s / 60.0
        turnback_min = 0.0 if route.closed else 2.0 * profile.turnback_s / 60.0
        return run_min + dwell_min + turnback_min

    def service_departures(self, service: Service, period_id: str) -> int:
        period = self.periods[period_id]
        headway = service.headway_by_period[period_id]
        return ceil((period.end_minute - period.start_minute) / headway)

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


def default_vehicle_type(mode: TransitMode) -> VehicleType:
    profile = REFERENCE_MODE_PROFILES[mode.value]
    names = {
        TransitMode.BUS: "Bus",
        TransitMode.TRAM: "Tram",
        TransitMode.METRO: "Metro",
        TransitMode.RAIL: "Rail",
    }
    return VehicleType(
        id=mode.value,
        name=names[mode],
        mode=mode,
        capacity=profile.capacity,
        operating_cost_per_km=profile.opex_per_vehicle_km,
    )


def _point_distance_km(left: Point, right: Point) -> float:
    return hypot(left.x - right.x, left.y - right.y) / 1000.0
