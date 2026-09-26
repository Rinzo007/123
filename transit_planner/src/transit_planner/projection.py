from __future__ import annotations

from math import cos, pi, radians

from .data import RoadRecord
from .geo import LineString, Point
from .network import Stop


EARTH_RADIUS_M = 6_378_137.0


def project_wgs84_point(
    point: Point,
    *,
    origin_lon: float,
    origin_lat: float,
) -> Point:
    """Local equirectangular projection suitable for city-scale calculations."""
    cos_lat = cos(radians(origin_lat))
    x = radians(point.x - origin_lon) * EARTH_RADIUS_M * cos_lat
    y = radians(point.y - origin_lat) * EARTH_RADIUS_M
    return Point(x, y)



def project_local_point_wgs84(
    point: Point,
    *,
    origin_lon: float,
    origin_lat: float,
) -> Point:
    """Inverse of project_wgs84_point for city-scale local coordinates."""
    cos_lat = cos(radians(origin_lat))
    if abs(cos_lat) < 1e-12:
        raise ValueError("origin_lat is too close to a pole")
    lon = origin_lon + point.x / (EARTH_RADIUS_M * cos_lat) * 180.0 / pi
    lat = origin_lat + point.y / EARTH_RADIUS_M * 180.0 / 3.141592653589793
    return Point(lon, lat)


def project_roads_wgs84(
    roads: tuple[RoadRecord, ...],
    *,
    origin_lon: float,
    origin_lat: float,
) -> tuple[RoadRecord, ...]:
    return tuple(
        RoadRecord(
            id=road.id,
            geometry=LineString(
                tuple(
                    project_wgs84_point(
                        point,
                        origin_lon=origin_lon,
                        origin_lat=origin_lat,
                    )
                    for point in road.geometry.points
                )
            ),
            speed_kph=road.speed_kph,
            road_type=road.road_type,
            oneway=road.oneway,
            connectors=road.connectors,
            length_m=road.length_m,
            forward_allowed=road.forward_allowed,
            backward_allowed=road.backward_allowed,
            prohibited_transitions=road.prohibited_transitions,
        )
        for road in roads
    )


def project_stops_wgs84(
    stops: tuple[Stop, ...],
    *,
    origin_lon: float,
    origin_lat: float,
) -> tuple[Stop, ...]:
    return tuple(
        Stop(
            id=stop.id,
            name=stop.name,
            location=project_wgs84_point(
                stop.location,
                origin_lon=origin_lon,
                origin_lat=origin_lat,
            ),
            is_station=stop.is_station,
        )
        for stop in stops
    )
