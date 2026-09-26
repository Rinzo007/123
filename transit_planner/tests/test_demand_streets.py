from transit_planner.city import DemandZone
from transit_planner.demand import ODPairDemand
from transit_planner.demand_streets import build_demand_streets, demand_streets_to_geojson


def test_build_demand_streets_from_zone_pairs():
    zones = {
        "a": DemandZone("a", 0, 0),
        "b": DemandZone("b", 1000, 0),
    }
    pairs = (ODPairDemand("a", "b", 100.0),)

    streets = build_demand_streets(pairs, zones)

    assert len(streets) == 1
    assert streets[0].trips == 100.0
    assert streets[0].distance_m == 1000.0


def test_demand_streets_geojson_contains_flow_properties():
    zones = {
        "a": DemandZone("a", 0, 0),
        "b": DemandZone("b", 1000, 0),
    }
    streets = build_demand_streets(
        (ODPairDemand("a", "b", 100.0),),
        zones,
    )

    geojson = demand_streets_to_geojson(
        streets,
        zones,
        origin_lon=39.2,
        origin_lat=51.67,
    )

    feature = geojson["features"][0]
    assert feature["properties"]["trips"] == 100.0
    assert feature["geometry"]["type"] == "LineString"
