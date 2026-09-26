from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum

from .geo import Point


class PlacePurpose(StrEnum):
    WORK = "work"
    EDUCATION = "education"
    SHOPPING = "shopping"
    HEALTH = "health"
    LEISURE = "leisure"
    AIRPORT = "airport"


@dataclass(frozen=True, slots=True)
class CityPlace:
    id: str
    name: str
    location: Point
    basic_category: str | None = None
    taxonomy_primary: str | None = None
    taxonomy_hierarchy: tuple[str, ...] = ()
    importance: float = 1.0

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("Place id cannot be empty")
        if not self.name.strip():
            raise ValueError("Place name cannot be empty")
        if self.importance < 0:
            raise ValueError("Place importance cannot be negative")


DEFAULT_PLACE_PURPOSE_CATEGORIES: dict[PlacePurpose, tuple[str, ...]] = {
    PlacePurpose.WORK: (
        "office",
        "corporate_office",
        "government_office",
        "business_center",
        "industrial_company",
    ),
    PlacePurpose.EDUCATION: (
        "school",
        "university",
        "college",
        "kindergarten",
    ),
    PlacePurpose.SHOPPING: (
        "shop",
        "shopping_mall",
        "supermarket",
        "department_store",
        "grocery_store",
    ),
    PlacePurpose.HEALTH: (
        "hospital",
        "clinic",
        "medical_center",
        "pharmacy",
    ),
    PlacePurpose.LEISURE: (
        "cinema",
        "theater",
        "museum",
        "stadium",
        "nightclub",
        "bar",
        "restaurant",
        "park",
        "attraction",
    ),
    PlacePurpose.AIRPORT: (
        "airport",
        "international_airport",
        "airport_terminal",
    ),
}


@dataclass(frozen=True, slots=True)
class PlacePurposeMapper:
    categories: dict[PlacePurpose, tuple[str, ...]] = field(default_factory=lambda: dict(DEFAULT_PLACE_PURPOSE_CATEGORIES))

    def purpose_for(self, place: CityPlace) -> PlacePurpose | None:
        categories = {
            place.basic_category,
            place.taxonomy_primary,
            *place.taxonomy_hierarchy,
        }
        for purpose, values in self.categories.items():
            if any(value in categories for value in values):
                return purpose
        return None

def aggregate_place_attractions(
    zones: tuple['DemandZone', ...],
    places: tuple[CityPlace, ...],
    *,
    mapper: PlacePurposeMapper | None = None,
) -> tuple['DemandZone', ...]:
    from .city import DemandZone

    mapper = mapper or PlacePurposeMapper()
    assigned = {zone.id: dict(zone.attractions) for zone in zones}

    for place in places:
        purpose = mapper.purpose_for(place)
        if purpose is None:
            continue

        nearest_zone = min(
            zones,
            key=lambda zone: (
                (place.location.x - zone.centroid_x) ** 2
                + (place.location.y - zone.centroid_y) ** 2
            ),
            default=None,
        )
        if nearest_zone is None:
            continue
        values = assigned[nearest_zone.id]
        values[purpose.value] = values.get(purpose.value, 0.0) + place.importance

    return tuple(
        replace(zone, purpose_attractions=tuple(sorted(assigned[zone.id].items())))
        for zone in zones
    )