from __future__ import annotations

from .data import ConnectorRecord, RoadRecord
from .network import Stop
from .places import CityPlace, PlacePurposeMapper
from .geo import Point
from .projection import project_local_point_wgs84


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
                    "purpose": (
                        purpose.value
                        if (purpose := PlacePurposeMapper().purpose_for(place)) is not None
                        else None
                    ),
                },
            }
            for place in places
        ],
    }


def zones_to_geojson(zones, *, origin_lon: float = 0.0, origin_lat: float = 0.0) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": zone.id,
                "geometry": {
                    "type": "Point",
                    "coordinates": _zone_wgs84_coordinates(
                        zone,
                        origin_lon=origin_lon,
                        origin_lat=origin_lat,
                    ),
                },
                "properties": {
                    "id": zone.id,
                    "population": zone.population,
                    "jobs": zone.jobs,
                    "purpose_attractions": dict(zone.purpose_attractions),
                },
            }
            for zone in zones
        ],
    }


def _zone_wgs84_coordinates(zone, *, origin_lon: float, origin_lat: float) -> list[float]:
    point = project_local_point_wgs84(
        Point(zone.centroid_x, zone.centroid_y),
        origin_lon=origin_lon,
        origin_lat=origin_lat,
    )
    return [point.x, point.y]
