from __future__ import annotations

from dataclasses import dataclass, field
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
