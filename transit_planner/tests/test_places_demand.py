from transit_planner.city import DemandZone
from transit_planner.geo import Point
from transit_planner.places import CityPlace, PlacePurposeMapper, aggregate_place_attractions
from transit_planner.reference_demand import build_demand_layers


def test_places_are_aggregated_to_nearest_zone_by_purpose():
    zones = (
        DemandZone("a", 0, 0, population=1000),
        DemandZone("b", 1000, 0, population=1000),
    )
    places = (
        CityPlace(
            "school",
            "School",
            Point(900, 0),
            basic_category="school",
            importance=2.0,
        ),
        CityPlace(
            "mall",
            "Mall",
            Point(100, 0),
            basic_category="shopping_mall",
            importance=3.0,
        ),
    )

    result = aggregate_place_attractions(zones, places)
    assert result[1].attractions["edu"] == 2.0
    assert result[0].attractions["shop"] == 3.0


def test_canonical_demand_uses_place_purpose_aliases():
    zones = (
        DemandZone("o", 0, 0, population=1000),
        DemandZone(
            "d",
            1000,
            0,
            population=500,
            purpose_attractions=(
                ("education", 100.0),
                ("shopping", 100.0),
                ("airport", 100.0),
            ),
        ),
    )
    layers = build_demand_layers(zones)
    totals = {layer.purpose: layer.total_trips for layer in layers.layers}

    assert totals["edu"] > 0
    assert totals["shop"] > 0
    assert totals["air"] > 0


def test_place_mapper_keeps_reference_layer_inputs_compatible():
    zones = (
        DemandZone("o", 0, 0, population=1000),
        DemandZone("d", 1000, 0, population=500),
    )
    places = (
        CityPlace("school", "School", Point(1000, 0), basic_category="school", importance=10),
        CityPlace("mall", "Mall", Point(1000, 0), basic_category="shopping_mall", importance=20),
    )
    enriched = aggregate_place_attractions(zones, places, mapper=PlacePurposeMapper())
    layers = build_demand_layers(enriched)
    totals = {layer.purpose: layer.total_trips for layer in layers.layers}
    assert totals["edu"] > 0
    assert totals["shop"] > 0
