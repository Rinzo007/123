from __future__ import annotations

from dataclasses import dataclass

from .city import DemandZone
from .demand import DemandMatrix
from .demand_profile import DEFAULT_TEMPORAL_DEMAND_PROFILE, TemporalDemandProfile
from .od import purpose_gravity_od
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
    config: CityDemandConfig = CityDemandConfig(),
    profile: TemporalDemandProfile = DEFAULT_TEMPORAL_DEMAND_PROFILE,
) -> DemandMatrix:
    enriched_zones = aggregate_place_attractions(zones, places)
    return purpose_gravity_od(
        enriched_zones,
        profile=profile,
        trip_rate=config.trip_rate,
        decay=config.decay,
        reference_speed_kph=config.reference_speed_kph,
    )
