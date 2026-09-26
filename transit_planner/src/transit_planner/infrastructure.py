from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class TrackType(StrEnum):
    SURFACE = "surface"
    ELEVATED = "elevated"
    TUNNEL = "tunnel"


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

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("Track section id cannot be empty")
        if self.length_km < 0:
            raise ValueError("Track length cannot be negative")
        if self.capacity_departures_per_hour <= 0:
            raise ValueError("Track capacity must be positive")
        if self.speed_limit_kph is not None and self.speed_limit_kph <= 0:
            raise ValueError("Track speed limit must be positive")


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
    capacities = [
        section.capacity_departures_per_hour
        for section in sections
        if section.shared_group is not None
    ]
    return min(capacities) if capacities else float("inf")
