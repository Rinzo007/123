from transit_planner.city import DemandZone
from transit_planner.city_demand import CityDemandConfig, build_city_demand
from transit_planner.geo import Point
from transit_planner.places import CityPlace, PlacePurpose


def test_build_city_demand_uses_places_and_population():
    zones = (
        DemandZone("a", 0, 0, population=1000, jobs=100),
        DemandZone("b", 1000, 0, population=500, jobs=800),
    )
    places = (
        CityPlace("school", "School", Point(1000, 0), basic_category="school", importance=10),
        CityPlace("mall", "Mall", Point(0, 0), basic_category="shopping_mall", importance=20),
    )

    demand = build_city_demand(
        zones,
        places,
        config=CityDemandConfig(trip_rate=0.1, decay=0.01),
    )

    assert demand.total_trips_per_day > 0
    assert any(pair.purpose == PlacePurpose.EDUCATION.value for pair in demand.pairs)
    assert any(pair.purpose == "shopping" for pair in demand.pairs)


def test_city_demand_is_zero_for_empty_population():
    zones = (
        DemandZone("a", 0, 0, population=0, jobs=0),
        DemandZone("b", 1000, 0, population=0, jobs=0),
    )

    demand = build_city_demand(zones, (), config=CityDemandConfig(trip_rate=0.1))

    assert demand.total_trips_per_day == 0
