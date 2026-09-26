from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .data import RoadDataProvider, RoadRecord


@dataclass(slots=True)
class CityDataset:
    roads: tuple[RoadRecord, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


class CityDataProvider:
    """Composable provider facade.

    Concrete sources are injected instead of imported by the simulation engine.
    """

    def __init__(self, *, roads: RoadDataProvider | None = None) -> None:
        self.roads_provider = roads

    def load(self) -> CityDataset:
        roads: tuple[RoadRecord, ...] = ()
        if self.roads_provider is not None:
            roads = self.roads_provider.load_roads()
        return CityDataset(roads=roads)
