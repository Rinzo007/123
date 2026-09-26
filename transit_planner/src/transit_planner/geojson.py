from __future__ import annotations

from .data import ConnectorRecord, RoadRecord
from .network import Stop
from .places import CityPlace


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
                    "length_m": road.length_m,
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


def places_to_geojson(places: tuple[CityPlace, ...]) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": place.id,
                "geometry": {
                    "type": "Point",
                    "coordinates": [place.location.x, place.location.y],
                },
                "properties": {
                    "id": place.id,
                    "name": place.name,
                    "basic_category": place.basic_category,
                    "taxonomy_primary": place.taxonomy_primary,
                    "importance": place.importance,
                },
            }
            for place in places
        ],
    }
