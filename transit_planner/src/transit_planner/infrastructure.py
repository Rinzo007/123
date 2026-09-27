from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class TrackType(StrEnum):
    SURFACE = "surface"
    ELEVATED = "elevated"
    TUNNEL = "tunnel"
    TRENCHED = "trenched"
    RAMP = "ramp"



class SignalDirection(StrEnum):
    FORWARD = "forward"
    REVERSE = "reverse"
    BOTH = "both"


@dataclass(frozen=True, slots=True)
class TrackNode:
    id: str
    x: float
    y: float
    elevation_m: float = 0.0

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("Track node id cannot be empty")


@dataclass(frozen=True, slots=True)
class Crossover:
    id: str
    from_track_id: str
    to_track_id: str
    position: float
    automatic: bool = False

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("Crossover id cannot be empty")
        if not 0.0 <= self.position <= 1.0:
            raise ValueError("Crossover position must be between 0 and 1")
        if self.from_track_id == self.to_track_id:
            raise ValueError("Crossover needs two different tracks")


@dataclass(frozen=True, slots=True)
class SignalBlock:
    id: str
    track_section_id: str
    start_position: float = 0.0
    end_position: float = 1.0
    direction: SignalDirection = SignalDirection.BOTH
    minimum_headway_seconds: float = 90.0

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("Signal block id cannot be empty")
        if not 0.0 <= self.start_position < self.end_position <= 1.0:
            raise ValueError("Invalid signal block positions")
        if self.minimum_headway_seconds <= 0:
            raise ValueError("Signal headway must be positive")


@dataclass(frozen=True, slots=True)
class TrackSection:
    id: str
    length_km: float
    track_type: TrackType = TrackType.SURFACE
    capacity_departures_per_hour: float = 30.0
    shared_group: str | None = None
    station_ids: tuple[str, ...] = ()
    speed_limit_kph: float | None = None
    start_node_id: str | None = None
    end_node_id: str | None = None
    start_elevation_m: float = 0.0
    end_elevation_m: float = 0.0
    max_slope_percent: float | None = None
    curve_radius_m: float | None = None
    track_count: int = 1
    direction: SignalDirection = SignalDirection.BOTH
    parallel_group: str | None = None
    grade_crossing_count: int = 0

    @property
    def elevation_delta_m(self) -> float:
        return self.end_elevation_m - self.start_elevation_m

    @property
    def slope_percent(self) -> float:
        return 0.0 if self.length_km <= 0 else abs(self.elevation_delta_m) / (self.length_km * 1000.0) * 100.0

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("Track section id cannot be empty")
        if self.length_km < 0:
            raise ValueError("Track length cannot be negative")
        if self.capacity_departures_per_hour <= 0:
            raise ValueError("Track capacity must be positive")
        if self.speed_limit_kph is not None and self.speed_limit_kph <= 0:
            raise ValueError("Track speed limit must be positive")
        if self.max_slope_percent is not None and self.max_slope_percent < 0:
            raise ValueError("max_slope_percent cannot be negative")
        if self.curve_radius_m is not None and self.curve_radius_m <= 0:
            raise ValueError("curve_radius_m must be positive")
        if self.track_count <= 0:
            raise ValueError("track_count must be positive")
        if self.grade_crossing_count < 0:
            raise ValueError("grade_crossing_count cannot be negative")



def shared_track_departure_capacity(
    sections: tuple[TrackSection, ...],
) -> float:
    """Return the bottleneck capacity for one shared corridor."""
    groups = {section.shared_group for section in sections if section.shared_group is not None}
    if len(groups) > 1:
        raise ValueError(
            "shared_track_departure_capacity expects sections from one shared_group"
        )
    capacities = [
        section.capacity_departures_per_hour
        for section in sections
        if section.shared_group is not None
    ]
    return min(capacities) if capacities else float("inf")


def shared_track_departure_capacities(
    sections: tuple[TrackSection, ...],
) -> dict[str, float]:
    """Return independent bottleneck capacities for every shared corridor."""
    grouped: dict[str, list[float]] = {}
    for section in sections:
        if section.shared_group is None:
            continue
        grouped.setdefault(section.shared_group, []).append(
            section.capacity_departures_per_hour
        )
    return {
        group: min(capacities)
        for group, capacities in grouped.items()
    }
