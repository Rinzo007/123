from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .geo import LineString, Point


@dataclass(frozen=True, slots=True)
class ConnectorRef:
    connector_id: str
    at: float

    def __post_init__(self) -> None:
        if not self.connector_id.strip():
            raise ValueError("connector_id cannot be empty")
        if not 0.0 <= self.at <= 1.0:
            raise ValueError("connector position must be in [0, 1]")


@dataclass(frozen=True, slots=True)
class ProhibitedTransitionSequenceEntry:
    segment_id: str
    connector_id: str

    def __post_init__(self) -> None:
        if not self.segment_id.strip():
            raise ValueError("segment_id cannot be empty")
        if not self.connector_id.strip():
            raise ValueError("connector_id cannot be empty")


@dataclass(frozen=True, slots=True)
class ProhibitedTransition:
    source_segment_id: str
    sequence: tuple[ProhibitedTransitionSequenceEntry, ...]
    final_heading: str | None = None
    when_heading: str | None = None

    def __post_init__(self) -> None:
        if not self.source_segment_id.strip():
            raise ValueError("source_segment_id cannot be empty")
        if not self.sequence:
            raise ValueError("A prohibited transition needs at least one sequence entry")
        if self.final_heading not in (None, "forward", "backward"):
            raise ValueError("Unsupported final heading")
        if self.when_heading not in (None, "forward", "backward"):
            raise ValueError("Unsupported source heading")


@dataclass(frozen=True, slots=True)
class ConnectorRecord:
    id: str
    location: Point


@dataclass(frozen=True, slots=True)
class RoadRecord:
    id: str
    geometry: LineString
    speed_kph: float
    road_type: str = "unknown"
    oneway: bool = False
    connectors: tuple[ConnectorRef, ...] = ()
    length_m: float | None = None
    forward_allowed: bool = True
    backward_allowed: bool = True
    prohibited_transitions: tuple[ProhibitedTransition, ...] = ()

    def __post_init__(self) -> None:
        if self.speed_kph <= 0:
            raise ValueError("Road speed must be positive")
        if self.length_m is not None and self.length_m <= 0:
            raise ValueError("Road length_m must be positive when provided")
        connector_ids = [ref.connector_id for ref in self.connectors]
        if len(connector_ids) != len(set(connector_ids)):
            raise ValueError(f"Duplicate connector reference in road {self.id}")


class RoadDataProvider(Protocol):
    def load_roads(self) -> tuple[RoadRecord, ...]: ...


class GeoJSONRoadProvider:
    """Load LineString road features from a GeoJSON object or file."""

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
                    length_m=(None if props.get("length_m") in (None, "") else float(props.get("length_m"))),
                )
            )
        return tuple(roads)
