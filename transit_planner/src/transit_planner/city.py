from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .geo import BoundingBox


@dataclass(frozen=True, slots=True)
class City:
    id: str
    name: str
    country: str
    bbox: BoundingBox
    source: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.name.strip():
            raise ValueError("City id and name cannot be empty")
        if not self.source.strip():
            raise ValueError("City source cannot be empty")


@dataclass(frozen=True, slots=True)
class DemandZone:
    id: str
    centroid_x: float
    centroid_y: float
    population: float = 0.0
    jobs: float = 0.0

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("Demand zone id cannot be empty")
        if self.population < 0 or self.jobs < 0:
            raise ValueError("Population and jobs cannot be negative")
