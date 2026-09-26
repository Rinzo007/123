from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .geo import LineString, Point


@dataclass(frozen=True, slots=True)
class RoadRecord:
    id: str
    geometry: LineString
    speed_kph: float
    road_type: str = "unknown"
    oneway: bool = False


class RoadDataProvider(Protocol):
    def load_roads(self) -> tuple[RoadRecord, ...]: ...


class GeoJSONRoadProvider:
    """Load LineString road features from a GeoJSON object or file.

    The coordinates are kept in the input coordinate system. Graph construction
    uses a supplied coordinate-to-metre conversion factor.
    """

    def __init__(self, source: str | Path | dict[str, Any]) -> None:
        self.source = source

    def _load_object(self) -> dict[str, Any]:
        if isinstance(self.source, dict):
            return self.source
        return json.loads(Path(self.source).read_text(encoding="utf-8"))

    def load_roads(self) -> tuple[RoadRecord, ...]:
        document = self._load_object()
        if document.get("type") != "FeatureCollection":
            raise ValueError("Expected GeoJSON FeatureCollection")

        roads: list[RoadRecord] = []
        for index, feature in enumerate(document.get("features", [])):
            geometry = feature.get("geometry") or {}
            if geometry.get("type") != "LineString":
                continue
            coordinates = geometry.get("coordinates", [])
            if len(coordinates) < 2:
                continue
            points = tuple(Point(float(x), float(y)) for x, y, *_ in coordinates)
            props = feature.get("properties") or {}
            speed = float(props.get("speed_kph", props.get("maxspeed", 30.0)))
            if speed <= 0:
                raise ValueError(f"Road feature {index} has non-positive speed")
            road_id = str(feature.get("id") or props.get("id") or f"road-{index}")
            roads.append(
                RoadRecord(
                    id=road_id,
                    geometry=LineString(points),
                    speed_kph=speed,
                    road_type=str(props.get("highway", "unknown")),
                    oneway=bool(props.get("oneway", False)),
                )
            )
        return tuple(roads)
