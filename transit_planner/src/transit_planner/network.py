from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from math import ceil, hypot, isfinite

from .geo import LineString, Point, minimum_curve_radius_m
from .infrastructure import Crossover, SignalBlock, TrackNode, TrackSection
from .stations import Platform, Station, StationGroup
from .rolling_stock import RollingStockType
from .fares import FareGroup
from .reference_model import (
    REFERENCE_MODE_PROFILES,
    REFERENCE_PERIODS,
    TrackRow,
    lateral_speed_limit_kph,
)


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
    row_by_segment: tuple[TrackRow, ...] = ()
    open_stop_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("Route id cannot be empty")
        if len(self.stop_ids) < 2:
            raise ValueError("A route needs at least two stops")
        expected_segments = len(self.stop_ids) if self.closed else len(self.stop_ids) - 1
        if self.track_section_ids and len(self.track_section_ids) != expected_segments:
            raise ValueError("track_section_ids must match route segments")
        if self.row_by_segment and len(self.row_by_segment) != expected_segments:
            raise ValueError("row_by_segment must match route segments")
        if self.open_stop_ids:
            if len(self.open_stop_ids) < 2:
                raise ValueError("A route needs at least two open stops")
            if len(self.open_stop_ids) != len(set(self.open_stop_ids)):
                raise ValueError("open_stop_ids must be unique")
            if any(stop_id not in self.stop_ids for stop_id in self.open_stop_ids):
                raise ValueError("open_stop_ids must be a subset of stop_ids")

    def segment_pairs(self) -> tuple[tuple[str, str], ...]:
        pairs = list(zip(self.stop_ids, self.stop_ids[1:]))
        if self.closed:
            pairs.append((self.stop_ids[-1], self.stop_ids[0]))
        return tuple(pairs)

    def is_stop_open(self, stop_id: str) -> bool:
        return not self.open_stop_ids or stop_id in self.open_stop_ids

    def open_stop_count(self) -> int:
        return sum(self.is_stop_open(stop_id) for stop_id in self.stop_ids)

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
    phase_by_period: dict[str, float] = field(default_factory=dict)

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
    stations: dict[str, Station] = field(default_factory=dict)
    platforms: dict[str, Platform] = field(default_factory=dict)
    station_groups: dict[str, StationGroup] = field(default_factory=dict)
    rolling_stock: dict[str, RollingStockType] = field(default_factory=dict)
    fare_groups: dict[str, FareGroup] = field(default_factory=dict)
    track_nodes: dict[str, TrackNode] = field(default_factory=dict)
    crossovers: dict[str, Crossover] = field(default_factory=dict)
    signal_blocks: dict[str, SignalBlock] = field(default_factory=dict)

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

    def add_track_node(self, node: TrackNode) -> None:
        self._add_unique(self.track_nodes, node.id, "track node")
        self.track_nodes[node.id] = node

    def add_crossover(self, crossover: Crossover) -> None:
        self._add_unique(self.crossovers, crossover.id, "crossover")
        if crossover.from_track_id not in self.track_sections or crossover.to_track_id not in self.track_sections:
            raise ValueError(f"Crossover {crossover.id} references unknown tracks")
        self.crossovers[crossover.id] = crossover

    def add_signal_block(self, block: SignalBlock) -> None:
        self._add_unique(self.signal_blocks, block.id, "signal block")
        if block.track_section_id not in self.track_sections:
            raise ValueError(f"Signal block {block.id} references unknown track")
        self.signal_blocks[block.id] = block

    def add_track_section(self, section: TrackSection) -> None:
        self._add_unique(self.track_sections, section.id, "track section")
        self.track_sections[section.id] = section

    def add_station(self, station: Station) -> None:
        self._add_unique(self.stations, station.id, "station")
        if station.stop_id not in self.stops:
            raise ValueError(f"Station {station.id} references unknown stop: {station.stop_id}")
        self.stations[station.id] = station

    def add_platform(self, platform: Platform) -> None:
        self._add_unique(self.platforms, platform.id, "platform")
        if platform.station_id not in self.stations:
            raise ValueError(f"Platform {platform.id} references unknown station: {platform.station_id}")
        missing_tracks = [track_id for track_id in platform.track_ids if track_id not in self.track_sections]
        if missing_tracks:
            raise ValueError(f"Platform {platform.id} references unknown tracks: {missing_tracks}")
        self.platforms[platform.id] = platform

    def add_station_group(self, group: StationGroup) -> None:
        self._add_unique(self.station_groups, group.id, "station group")
        missing = [station_id for station_id in group.station_ids if station_id not in self.stations]
        if missing:
            raise ValueError(f"Station group {group.id} references unknown stations: {missing}")
        self.station_groups[group.id] = group

    def add_rolling_stock(self, stock: RollingStockType) -> None:
        self._add_unique(self.rolling_stock, stock.id, "rolling stock")
        if stock.vehicle_type_id not in self.vehicle_types:
            raise ValueError(f"Rolling stock {stock.id} references unknown vehicle type: {stock.vehicle_type_id}")
        self.rolling_stock[stock.id] = stock

    def add_fare_group(self, group: FareGroup) -> None:
        self._add_unique(self.fare_groups, group.id, "fare group")
        self.fare_groups[group.id] = group

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
        invalid_phases = [pid for pid, phase in service.phase_by_period.items()
                          if pid not in self.periods or phase < 0 or phase >= 1440]
        if invalid_phases:
            raise ValueError(f"Service {service.id} references invalid phase periods: {invalid_phases}")
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
            for section_id in route.track_section_ids:
                if section_id not in self.track_sections:
                    errors.append(f"Route {route.id} references unknown track {section_id}")
        for section in self.track_sections.values():
            if section.start_node_id and section.start_node_id not in self.track_nodes:
                errors.append(f"Track {section.id} references unknown start node {section.start_node_id}")
            if section.end_node_id and section.end_node_id not in self.track_nodes:
                errors.append(f"Track {section.id} references unknown end node {section.end_node_id}")
            if section.max_slope_percent is not None and section.slope_percent > section.max_slope_percent + 1e-9:
                errors.append(
                    f"Track {section.id} slope {section.slope_percent:.2f}% exceeds "
                    f"{section.max_slope_percent:.2f}%"
                )
        for platform in self.platforms.values():
            if platform.station_id not in self.stations:
                errors.append(f"Platform {platform.id} references unknown station {platform.station_id}")
            for track_id in platform.track_ids:
                if track_id not in self.track_sections:
                    errors.append(f"Platform {platform.id} references unknown track {track_id}")
        for block in self.signal_blocks.values():
            if block.track_section_id not in self.track_sections:
                errors.append(f"Signal block {block.id} references unknown track {block.track_section_id}")
        for service in self.services.values():
            if service.route_id not in self.routes:
                errors.append(f"Service {service.id} references unknown route {service.route_id}")
            if service.vehicle_type_id not in self.vehicle_types:
                errors.append(f"Service {service.id} references unknown vehicle type {service.vehicle_type_id}")
            for period_id, headway in service.headway_by_period.items():
                if headway <= 0:
                    errors.append(f"Service {service.id} has invalid headway in {period_id}")
                route = self.routes.get(service.route_id)
                if route and route.track_section_ids:
                    tph = 60.0 / headway
                    for section_id in route.track_section_ids:
                        section = self.track_sections[section_id]
                        if tph > section.capacity_departures_per_hour + 1e-9:
                            errors.append(
                                f"Service {service.id} exceeds track {section_id} capacity "
                                f"({tph:.2f} > {section.capacity_departures_per_hour:.2f} tph)"
                            )
        return errors

    def route_segment_geometry_points(
        self,
        route: Route,
        index: int,
    ) -> tuple[Point, ...]:
        """Return the geometry slice corresponding to one route segment."""
        if route.geometry is None:
            return ()
        pairs = route.segment_pairs()
        if index < 0 or index >= len(pairs):
            raise IndexError("segment index out of range")
        stop_points = tuple(
            self.stops[stop_id].location
            for stop_id in route.stop_ids
        )
        points = route.geometry.points
        matched = _match_geometry_to_stops(points, stop_points, route.closed)
        if matched is None:
            return ()
        geometry_points, indices = matched
        start = indices[index]
        if route.closed and index == len(pairs) - 1:
            first = indices[0]
            return geometry_points[start:] + geometry_points[: first + 1]
        end = indices[index + 1]
        if end < start:
            return ()
        return geometry_points[start : end + 1]


    def route_segment_length_km(self, route: Route, index: int) -> float:
        left_id, right_id = route.segment_pairs()[index]
        section_id = route.track_section_for_segment(index)
        if section_id is not None:
            return self.track_sections[section_id].length_km
        geometry = self.route_segment_geometry_points(route, index)
        if len(geometry) >= 2:
            return sum(
                hypot(b.x - a.x, b.y - a.y)
                for a, b in zip(geometry, geometry[1:])
            ) / 1000.0
        return _point_distance_km(
            self.stops[left_id].location,
            self.stops[right_id].location,
        )

    def route_segment_row(self, route: Route, index: int) -> TrackRow:
        """Return the reference infrastructure row for a route segment."""
        if index < 0 or index >= len(route.segment_pairs()):
            raise IndexError("segment index out of range")
        if route.row_by_segment:
            return route.row_by_segment[index]
        return REFERENCE_MODE_PROFILES[route.mode.value].default_row

    def route_segment_cost_per_km(self, route: Route, index: int) -> float:
        profile = REFERENCE_MODE_PROFILES[route.mode.value]
        row = self.route_segment_row(route, index)
        return profile.rows[row].cost_per_km

    def route_segment_run_time_min(self, route: Route, index: int) -> float:
        profile = REFERENCE_MODE_PROFILES[route.mode.value]
        section_id = route.track_section_for_segment(index)
        row = self.route_segment_row(route, index)
        speed = profile.rows[row].speed_kph
        if section_id is not None:
            section = self.track_sections[section_id]
            if section.speed_limit_kph is not None:
                speed = section.speed_limit_kph
        geometry = self.route_segment_geometry_points(route, index)
        if len(geometry) >= 3:
            radius_m = minimum_curve_radius_m(geometry)
            if isfinite(radius_m):
                speed = min(
                    speed,
                    lateral_speed_limit_kph(route.mode.value, radius_m),
                )
        if speed <= 0:
            raise ValueError("Route segment speed must be positive")
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
        dwell_min = (
            2.0
            * route.open_stop_count()
            * profile.dwell_s
            / 60.0
        )
        turnback_min = 0.0 if route.closed else 2.0 * profile.turnback_s / 60.0
        return run_min + dwell_min + turnback_min

    def service_departures(self, service: Service, period_id: str) -> int:
        period = self.periods[period_id]
        headway = service.headway_by_period[period_id]
        offset = service.departure_offset_by_period.get(period_id, 0.0)
        first = period.start_minute + ((offset - period.start_minute) % headway)
        if first >= period.end_minute:
            return 0
        return ceil((period.end_minute - first) / headway)

    @staticmethod
    def _add_unique(collection: dict[str, object], item_id: str, kind: str) -> None:
        if not item_id.strip():
            raise ValueError(f"{kind.title()} id cannot be empty")
        if item_id in collection:
            raise ValueError(f"Duplicate {kind} id: {item_id}")


def _match_geometry_to_stops(
    points: tuple[Point, ...],
    stops: tuple[Point, ...],
    closed: bool,
) -> tuple[tuple[Point, ...], tuple[int, ...]] | None:
    if len(points) < 2 or len(stops) < 2:
        return None

    def match(
        sequence: tuple[Point, ...],
    ) -> tuple[tuple[Point, ...], tuple[int, ...], float] | None:
        if not closed:
            indices: list[int] = []
            cursor = 0
            score = 0.0
            for stop in stops:
                if cursor >= len(sequence):
                    return None
                index = min(
                    range(cursor, len(sequence)),
                    key=lambda item: _point_distance_m(sequence[item], stop),
                )
                if indices and index <= indices[-1]:
                    return None
                score += _point_distance_m(sequence[index], stop) ** 2
                indices.append(index)
                cursor = index + 1
            return sequence, tuple(indices), score

        first = min(
            range(len(sequence)),
            key=lambda item: _point_distance_m(sequence[item], stops[0]),
        )
        indices = [first]
        score = _point_distance_m(sequence[first], stops[0]) ** 2
        cursor = first
        for stop in stops[1:]:
            candidates = [
                first + offset
                for offset in range(1, len(sequence) + 1)
            ]
            index = min(
                candidates,
                key=lambda item: _point_distance_m(
                    sequence[item % len(sequence)],
                    stop,
                ),
            )
            if index <= cursor:
                return None
            score += _point_distance_m(
                sequence[index % len(sequence)],
                stop,
            ) ** 2
            indices.append(index % len(sequence))
            cursor = index
        return sequence, tuple(indices), score

    candidates = [match(points)]
    if not closed:
        candidates.append(match(tuple(reversed(points))))
    valid = [item for item in candidates if item is not None]
    if not valid:
        return None
    sequence, indices, _score = min(valid, key=lambda item: item[2])
    return sequence, indices


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


def _point_distance_m(left: Point, right: Point) -> float:
    return hypot(left.x - right.x, left.y - right.y)


def _point_distance_km(left: Point, right: Point) -> float:
    return hypot(left.x - right.x, left.y - right.y) / 1000.0
