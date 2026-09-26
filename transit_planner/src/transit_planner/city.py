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
    purpose_attractions: tuple[tuple[str, float], ...] = ()

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("Demand zone id cannot be empty")
        if self.population < 0 or self.jobs < 0:
            raise ValueError("Population and jobs cannot be negative")
        if any(value < 0 for _, value in self.purpose_attractions):
            raise ValueError("Purpose attractions cannot be negative")
        keys = [purpose for purpose, _ in self.purpose_attractions]
        if len(keys) != len(set(keys)):
            raise ValueError("Purpose attractions must have unique purposes")

    @property
    def attractions(self) -> dict[str, float]:
        return dict(self.purpose_attractions)
