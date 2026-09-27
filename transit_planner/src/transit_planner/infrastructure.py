from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class TrackType(StrEnum):
    SURFACE = "surface"
    ELEVATED = "elevated"
    TUNNEL = "tunnel"



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
class ConstructionRates:
    cost_per_km: dict[TrackType, float]
    station_cost: float = 0.0
    parallel_track_multiplier: float = 1.0

    def __post_init__(self) -> None:
        if any(value < 0 for value in self.cost_per_km.values()):
            raise ValueError("Construction costs cannot be negative")
        if self.station_cost < 0:
            raise ValueError("station_cost cannot be negative")
        if self.parallel_track_multiplier < 0:
            raise ValueError("parallel_track_multiplier cannot be negative")


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


@dataclass(frozen=True, slots=True)
class ConstructionProject:
    id: str
    name: str
    sections: tuple[TrackSection, ...]
    parallel: bool = False
    station_count: int = 0

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.name.strip():
            raise ValueError("Project id and name cannot be empty")
        if self.station_count < 0:
            raise ValueError("station_count cannot be negative")


@dataclass(frozen=True, slots=True)
class ProjectCost:
    project_id: str
    construction_cost: float
    station_cost: float
    total_cost: float


@dataclass(slots=True)
class YearPlan:
    year: int
    projects: list[ConstructionProject] = field(default_factory=list)
    removals: list[str] = field(default_factory=list)

    def add_project(self, project: ConstructionProject) -> None:
        if any(existing.id == project.id for existing in self.projects):
            raise ValueError(f"Duplicate project: {project.id}")
        self.projects.append(project)

    def remove_project(self, project_id: str) -> None:
        self.projects = [project for project in self.projects if project.id != project_id]

    def reserve_removal(self, asset_id: str) -> None:
        if asset_id and asset_id not in self.removals:
            self.removals.append(asset_id)


def estimate_project_cost(
    project: ConstructionProject,
    *,
    rates: ConstructionRates,
) -> ProjectCost:
    construction = sum(
        section.length_km * rates.cost_per_km.get(section.track_type, 0.0)
        for section in project.sections
    )
    if project.parallel:
        construction *= rates.parallel_track_multiplier

    station_cost = project.station_count * rates.station_cost
    return ProjectCost(
        project_id=project.id,
        construction_cost=construction,
        station_cost=station_cost,
        total_cost=construction + station_cost,
    )


def total_reserved_capital(
    plan: YearPlan,
    *,
    rates: ConstructionRates,
) -> float:
    return sum(
        estimate_project_cost(project, rates=rates).total_cost
        for project in plan.projects
    )


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
