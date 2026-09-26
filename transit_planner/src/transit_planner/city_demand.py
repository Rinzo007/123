from __future__ import annotations

from dataclasses import dataclass

from .city import DemandZone
from .demand import DemandMatrix
from .demand_profile import DEFAULT_TEMPORAL_DEMAND_PROFILE, TemporalDemandProfile
from .od import purpose_gravity_od
from .geo import Point
from .projection import project_wgs84_point
from .places import CityPlace, aggregate_place_attractions


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
    profile: TemporalDemandProfile = DEFAULT_TEMPORAL_DEMAND_PROFILE,
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
                importance=place.importance,
            )
            for place in places
        )

    enriched_zones = aggregate_place_attractions(zones, places)
    return purpose_gravity_od(
        enriched_zones,
        profile=profile,
        trip_rate=config.trip_rate,
        decay=config.decay,
        reference_speed_kph=config.reference_speed_kph,
    )
