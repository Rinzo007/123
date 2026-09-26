from transit_planner.city import DemandZone
from transit_planner.city_demand import build_city_demand
from transit_planner.geo import Point
from transit_planner.places import CityPlace


def test_build_city_demand_uses_places_and_population():
    zones = (
        DemandZone("a", 0, 0, population=1000, jobs=100),
        DemandZone("b", 1000, 0, population=500, jobs=800),
    )
    places = (
        CityPlace("school", "School", Point(39.211, 51.67), basic_category="school", importance=10),
        CityPlace("mall", "Mall", Point(39.2, 51.67), basic_category="shopping_mall", importance=20),
    )

    demand = build_city_demand(
        zones,
        places,
        origin_lon=39.2,
        origin_lat=51.67,
    )

    assert demand.total_trips_per_day > 0
    assert any(pair.purpose == "edu" for pair in demand.pairs)
    assert any(pair.purpose == "shop" for pair in demand.pairs)


def test_city_demand_is_zero_for_empty_population():
    zones = (
        DemandZone("a", 0, 0, population=0, jobs=0),
        DemandZone("b", 1000, 0, population=0, jobs=0),
    )

    demand = build_city_demand(zones, ())

    assert demand.total_trips_per_day == 0
