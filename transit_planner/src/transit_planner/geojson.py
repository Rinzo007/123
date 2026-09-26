from __future__ import annotations

from .data import RoadRecord
from .network import Stop


def roads_to_geojson(roads: tuple[RoadRecord, ...]) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": road.id,
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [point.x, point.y]
                        for point in road.geometry.points
                    ],
                },
                "properties": {
                    "id": road.id,
                    "speed_kph": road.speed_kph,
                    "road_type": road.road_type,
                    "oneway": road.oneway,
                },
            }
            for road in roads
        ],
    }


def stops_to_geojson(stops: tuple[Stop, ...]) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": stop.id,
                "geometry": {
                    "type": "Point",
                    "coordinates": [stop.location.x, stop.location.y],
                },
                "properties": {
                    "id": stop.id,
                    "name": stop.name,
                    "is_station": stop.is_station,
                },
            }
            for stop in stops
        ],
    }
