from __future__ import annotations

from dataclasses import dataclass
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
    categories: dict[PlacePurpose, tuple[str, ...]] = DEFAULT_PLACE_PURPOSE_CATEGORIES

    def purpose_for(self, place: CityPlace) -> PlacePurpose | None:
        categories = {
            place.basic_category,
            place.taxonomy_primary,
        }
        for purpose, values in self.categories.items():
            if any(value in categories for value in values):
                return purpose
        return None
