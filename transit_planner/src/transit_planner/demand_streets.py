from __future__ import annotations

from dataclasses import dataclass
from math import sqrt


@dataclass(frozen=True, slots=True)
class DemandStreet:
    origin_zone_id: str
    destination_zone_id: str
    trips: float
    distance_m: float


def build_demand_streets(
    pairs,
    zones,
    *,
    min_trips: float = 0.0,
) -> tuple[DemandStreet, ...]:
    result: list[DemandStreet] = []
    for pair in pairs:
        if pair.trips_per_day < min_trips:
            continue
        origin = zones.get(pair.origin_zone_id)
        destination = zones.get(pair.destination_zone_id)
        if origin is None or destination is None:
            continue
        distance_m = sqrt(
            (origin.centroid_x - destination.centroid_x) ** 2
            + (origin.centroid_y - destination.centroid_y) ** 2
        )
        result.append(
            DemandStreet(
                origin_zone_id=pair.origin_zone_id,
                destination_zone_id=pair.destination_zone_id,
                trips=pair.trips_per_day,
                distance_m=distance_m,
            )
        )
    return tuple(result)


def demand_streets_to_geojson(
    streets: tuple[DemandStreet, ...],
    zones,
    *,
    origin_lon: float = 0.0,
    origin_lat: float = 0.0,
    scale: float = 1.0,
) -> dict:
    from .projection import project_local_point_wgs84
    from .geo import Point

    features = []
    for street in streets:
        origin = zones.get(street.origin_zone_id)
        destination = zones.get(street.destination_zone_id)
        if origin is None or destination is None:
            continue

        start = project_local_point_wgs84(
            Point(origin.centroid_x, origin.centroid_y),
            origin_lon=origin_lon,
            origin_lat=origin_lat,
        )
        end = project_local_point_wgs84(
            Point(destination.centroid_x, destination.centroid_y),
            origin_lon=origin_lon,
            origin_lat=origin_lat,
        )
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[start.x, start.y], [end.x, end.y]],
                },
                "properties": {
                    "origin_zone_id": street.origin_zone_id,
                    "destination_zone_id": street.destination_zone_id,
                    "trips": street.trips,
                    "distance_m": street.distance_m,
                    "flow_weight": street.trips * scale,
                },
            }
        )

    return {"type": "FeatureCollection", "features": features}
