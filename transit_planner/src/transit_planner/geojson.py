from __future__ import annotations

from .data import ConnectorRecord, RoadRecord
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
                    "connector_count": len(road.connectors),
                    "connector_ids": [
                        ref.connector_id for ref in road.connectors
                    ],
                },
            }
            for road in roads
        ],
    }


def connectors_to_geojson(
    connectors: tuple[ConnectorRecord, ...],
) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": connector.id,
                "geometry": {
                    "type": "Point",
                    "coordinates": [
                        connector.location.x,
                        connector.location.y,
                    ],
                },
                "properties": {
                    "id": connector.id,
                },
            }
            for connector in connectors
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
