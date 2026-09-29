import pytest

from transit_planner.city_pack import build_city_zones
from transit_planner.geo import Point
from transit_planner.places import CityPlace
from transit_planner.urban import (
    FLOOR_AREA_PER_PERSON_M2,
    BuildingFootprint,
    effective_floors,
    is_residential_building,
    population_from_buildings,
    residential_floor_area_m2,
)


def _square(center_lon: float, center_lat: float, side_deg: float):
    """Квадратный контур вокруг центра: чего-то, что можно посчитать."""
    half = side_deg / 2
    return (
        Point(center_lon - half, center_lat - half),
        Point(center_lon + half, center_lat - half),
        Point(center_lon + half, center_lat + half),
        Point(center_lon - half, center_lat + half),
    )


def _building(
    *,
    area_m2: float = 100.0,
    building_class: str | None = "apartments",
    num_floors: float | None = 4.0,
    height_m: float | None = None,
) -> BuildingFootprint:
    return BuildingFootprint(
        id="b1",
        polygons=(_square(13.4, 52.5, 0.0001),),
        area_m2=area_m2,
        building_class=building_class,
        num_floors=num_floors,
        height_m=height_m,
    )


def test_population_counts_floor_area_not_footprint():
    """Люди живут на этажах, поэтому этажность входит в площадь."""
    building = _building(area_m2=100.0, num_floors=4.0)
    assert residential_floor_area_m2(building) == pytest.approx(400.0)
    assert population_from_buildings([building]) == pytest.approx(
        400.0 / FLOOR_AREA_PER_PERSON_M2
    )


def test_non_residential_buildings_carry_no_population():
    """Склад или офис нельзя считать жильём: класс важнее подтипа."""
    warehouse = _building(area_m2=1000.0, num_floors=6.0, building_class="warehouse")
    assert not is_residential_building(warehouse)
    assert residential_floor_area_m2(warehouse) == 0.0
    assert population_from_buildings([warehouse]) == 0.0


def test_underground_buildings_are_skipped():
    buried = BuildingFootprint(
        id="b2",
        polygons=(_square(13.4, 52.5, 0.0001),),
        area_m2=200.0,
        building_class="apartments",
        is_underground=True,
        num_floors=3.0,
    )
    assert population_from_buildings([buried]) == 0.0


def test_floors_fall_back_to_height_then_to_minimum():
    from_height = _building(num_floors=None, height_m=12.0)
    assert effective_floors(from_height) == pytest.approx(4.0)
    bare = _building(num_floors=None, height_m=None)
    assert effective_floors(bare) == pytest.approx(1.0)
    # Нереальные значения не должны завышать оценку.
    assert effective_floors(_building(num_floors=500.0)) == pytest.approx(8.0)


def test_subtype_used_when_class_missing():
    building = BuildingFootprint(
        id="b3",
        polygons=(_square(13.4, 52.5, 0.0001),),
        area_m2=100.0,
        subtype="residential",
        num_floors=2.0,
    )
    assert is_residential_building(building)


def test_zones_take_population_from_buildings():
    places = tuple(
        CityPlace(
            id=f"p{index}",
            name=f"Place {index}",
            location=Point(13.4 + index * 0.002, 52.5),
            basic_category="cafe",
            importance=1.0,
        )
        for index in range(3)
    )
    buildings = tuple(
        BuildingFootprint(
            id=f"b{index}",
            polygons=(_square(13.4 + index * 0.002, 52.5, 0.0001),),
            area_m2=1000.0,
            building_class="apartments",
            num_floors=4.0,
        )
        for index in range(3)
    )
    zones = build_city_zones(
        places, origin_lon=13.4, origin_lat=52.5, buildings=buildings
    )
    total = sum(zone.population for zone in zones)
    expected = 3 * 1000.0 * 4.0 / FLOOR_AREA_PER_PERSON_M2
    assert total == pytest.approx(expected, rel=1e-6)
    assert total > sum(zone.jobs for zone in zones)


def test_zones_fall_back_to_places_without_buildings():
    """Без зданий поведение прежнее: население из прокси мест."""
    places = (
        CityPlace(
            id="p0",
            name="Supermarket",
            location=Point(13.4, 52.5),
            basic_category="supermarket",
            importance=5.0,
        ),
    )
    zones = build_city_zones(places, origin_lon=13.4, origin_lat=52.5)
    assert sum(zone.population for zone in zones) == pytest.approx(5.0)


def test_population_rejects_non_positive_area_per_person():
    with pytest.raises(ValueError):
        population_from_buildings([_building()], floor_area_per_person_m2=0.0)
