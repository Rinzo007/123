from __future__ import annotations

import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .data import RoadRecord
from .geo import LineString, Point
from .network import Stop

DEFAULT_OVERPASS_ENDPOINT = "https://overpass-api.de/api/interpreter"


class OverpassRoadProvider:
    """Fetch road LineStrings from an Overpass API endpoint."""

    def __init__(
        self,
        bbox: tuple[float, float, float, float],
        *,
        endpoint: str = DEFAULT_OVERPASS_ENDPOINT,
        timeout_seconds: float = 60.0,
        road_filter: str = '["highway"]["area"!="yes"]',
    ) -> None:
        self.bbox = bbox
        self.endpoint = endpoint
        self.timeout_seconds = timeout_seconds
        self.road_filter = road_filter

    def query(self) -> dict:
        south, west, north, east = self.bbox
        query = (
            "[out:json][timeout:55];"
            f"way{self.road_filter}({south},{west},{north},{east});"
            "out geom;"
        )
        return _post_json(self.endpoint, query, self.timeout_seconds)

    def load_roads(self) -> tuple[RoadRecord, ...]:
        document = self.query()
        roads: list[RoadRecord] = []
        for element in document.get("elements", []):
            geometry = element.get("geometry") or []
            if len(geometry) < 2:
                continue
            tags = element.get("tags") or {}
            points = tuple(
                Point(float(item["lon"]), float(item["lat"]))
                for item in geometry
            )
            roads.append(
                RoadRecord(
                    id=f"osm:{element['id']}",
                    geometry=LineString(points),
                    speed_kph=_parse_speed(tags.get("maxspeed")),
                    road_type=str(tags.get("highway", "unknown")),
                    oneway=str(tags.get("oneway", "")).lower() in {"yes", "true", "1"},
                )
            )
        return tuple(roads)


class OverpassStopProvider:
    """Fetch public-transport stops/platforms from OpenStreetMap."""

    def __init__(
        self,
        bbox: tuple[float, float, float, float],
        *,
        endpoint: str = DEFAULT_OVERPASS_ENDPOINT,
        timeout_seconds: float = 60.0,
    ) -> None:
        self.bbox = bbox
        self.endpoint = endpoint
        self.timeout_seconds = timeout_seconds

    def query(self) -> dict:
        south, west, north, east = self.bbox
        query = (
            "[out:json][timeout:55];"
            "("
            f'node["highway"="bus_stop"]({south},{west},{north},{east});'
            f'node["public_transport"="platform"]({south},{west},{north},{east});'
            f'node["public_transport"="stop_position"]({south},{west},{north},{east});'
            ");out;"
        )
        return _post_json(self.endpoint, query, self.timeout_seconds)

    def load_stops(self) -> tuple[Stop, ...]:
        document = self.query()
        stops: dict[str, Stop] = {}
        for element in document.get("elements", []):
            if element.get("type") != "node":
                continue
            tags = element.get("tags") or {}
            stop_id = f"osm:{element['id']}"
            name = str(tags.get("name") or tags.get("local_ref") or stop_id)
            stops[stop_id] = Stop(
                id=stop_id,
                name=name,
                location=Point(float(element["lon"]), float(element["lat"])),
                is_station=tags.get("public_transport") in {"station", "stop_area"},
            )
        return tuple(stops.values())


def _post_json(endpoint: str, query: str, timeout: float) -> dict:
    payload = urlencode({"data": query}).encode("utf-8")
    request = Request(
        endpoint,
        data=payload,
        headers={"User-Agent": "TransitPlanner/0.1 (+https://github.com/Rinzo007/123)"},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _parse_speed(value: str | None) -> float:
    if not value:
        return 30.0
    normalized = value.lower().replace("km/h", "").replace("kph", "").strip()
    try:
        return max(5.0, float(normalized.split(";")[0].split()[0]))
    except (ValueError, IndexError):
        return 30.0
