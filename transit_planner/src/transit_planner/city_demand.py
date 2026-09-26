from __future__ import annotations

from .city import DemandZone
from .demand import DemandMatrix
from .places import CityPlace, aggregate_place_attractions
from .projection import project_wgs84_point
from .reference_demand import ReferenceDemandLayers, build_daily_demand, build_demand_layers


def _project_places(
    places: tuple[CityPlace, ...],
    *,
    origin_lon: float | None,
    origin_lat: float | None,
) -> tuple[CityPlace, ...]:
    if (origin_lon is None) != (origin_lat is None):
        raise ValueError("origin_lon and origin_lat must be provided together")
    if origin_lon is None or origin_lat is None:
        return places
    return tuple(
        CityPlace(
            id=place.id,
            name=place.name,
            location=project_wgs84_point(
                place.location,
                origin_lon=origin_lon,
                origin_lat=origin_lat,
            ),
            basic_category=place.basic_category,
            taxonomy_primary=place.taxonomy_primary,
            taxonomy_hierarchy=place.taxonomy_hierarchy,
            importance=place.importance,
        )
        for place in places
    )


def build_city_demand(
    zones: tuple[DemandZone, ...],
    places: tuple[CityPlace, ...] = (),
    *,
    origin_lon: float | None = None,
    origin_lat: float | None = None,
    config: CityDemandConfig = CityDemandConfig(),
) -> DemandMatrix:
    projected_places = _project_places(
        places,
        origin_lon=origin_lon,
        origin_lat=origin_lat,
    )
    enriched_zones = aggregate_place_attractions(zones, projected_places)
    return build_daily_demand(
        enriched_zones,
        trip_rate=config.trip_rate,
        decay=config.decay,
        reference_speed_kph=config.reference_speed_kph,
    )


def build_city_demand_layers(
    zones: tuple[DemandZone, ...],
    places: tuple[CityPlace, ...] = (),
    *,
    origin_lon: float | None = None,
    origin_lat: float | None = None,
) -> ReferenceDemandLayers:
    projected_places = _project_places(
        places,
        origin_lon=origin_lon,
        origin_lat=origin_lat,
    )
    enriched_zones = aggregate_place_attractions(zones, projected_places)
    return build_demand_layers(enriched_zones)


def build_city_daily_demand(
    zones: tuple[DemandZone, ...],
    places: tuple[CityPlace, ...] = (),
    *,
    origin_lon: float | None = None,
    origin_lat: float | None = None,
) -> DemandMatrix:
    return build_city_demand(
        zones,
        places,
        origin_lon=origin_lon,
        origin_lat=origin_lat,
    )
