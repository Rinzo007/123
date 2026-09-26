from __future__ import annotations

from dataclasses import dataclass

from .city import DemandZone
from .demand import DemandMatrix
from .projection import project_wgs84_point
from .places import CityPlace, aggregate_place_attractions
from .reference_demand import ReferenceDemandLayers, build_daily_demand, build_demand_layers


@dataclass(frozen=True, slots=True)
class CityDemandConfig:
    trip_rate: float = 0.12
    decay: float = 0.08
    reference_speed_kph: float = 30.0

    def __post_init__(self) -> None:
        if self.trip_rate < 0:
            raise ValueError("trip_rate cannot be negative")
        if self.decay <= 0:
            raise ValueError("decay must be positive")
        if self.reference_speed_kph <= 0:
            raise ValueError("reference_speed_kph must be positive")


def build_city_demand(
    zones: tuple[DemandZone, ...],
    places: tuple[CityPlace, ...],
    *,
    origin_lon: float | None = None,
    origin_lat: float | None = None,
    config: CityDemandConfig = CityDemandConfig(),
) -> DemandMatrix:
    if (origin_lon is None) != (origin_lat is None):
        raise ValueError("origin_lon and origin_lat must be provided together")

    if origin_lon is not None and origin_lat is not None:
        places = tuple(
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

    enriched_zones = aggregate_place_attractions(zones, places)
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
    if (origin_lon is None) != (origin_lat is None):
        raise ValueError("origin_lon and origin_lat must be provided together")
    if origin_lon is not None and origin_lat is not None:
        places = tuple(
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
    enriched_zones = aggregate_place_attractions(zones, places)
    return build_demand_layers(enriched_zones)


def build_city_daily_demand(
    zones: tuple[DemandZone, ...],
    places: tuple[CityPlace, ...] = (),
    *,
    origin_lon: float | None = None,
    origin_lat: float | None = None,
) -> DemandMatrix:
    layers = build_city_demand_layers(
        zones,
        places,
        origin_lon=origin_lon,
        origin_lat=origin_lat,
    )
    pairs: list[ODPairDemand] = []
    for layer in layers.layers:
        totals: dict[tuple[str, str], float] = {}
        for pair in layer.demand.pairs:
            key = (pair.origin_zone_id, pair.destination_zone_id)
            totals[key] = totals.get(key, 0.0) + pair.trips
        pairs.extend(
            ODPairDemand(origin, destination, trips, layer.purpose)
            for (origin, destination), trips in totals.items()
        )
    return DemandMatrix(tuple(pairs))
