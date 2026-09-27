from transit_planner.api import reference_demand
from transit_planner.city import DemandZone


def test_reference_demand_exposes_separate_commuter_and_purpose_layers(monkeypatch):
    zones = (
        DemandZone(
            "z1",
            0.0,
            0.0,
            population=100.0,
            jobs=50.0,
            purpose_attractions=(("edu", 40.0),),
        ),
        DemandZone(
            "z2",
            1000.0,
            0.0,
            population=120.0,
            jobs=80.0,
            purpose_attractions=(("shop", 100.0), ("health", 25.0)),
        ),
        DemandZone(
            "z3",
            2000.0,
            0.0,
            population=80.0,
            jobs=60.0,
            purpose_attractions=(("air", 10.0), ("night", 75.0)),
        ),
    )

    monkeypatch.setenv("TRANSIT_PLANNER_POPULATION_RASTER", "synthetic.tif")
    monkeypatch.setattr(
        "transit_planner.api.generate_zones_from_population_raster",
        lambda *_args, **_kwargs: zones,
    )
    monkeypatch.setattr(
        "transit_planner.api.OverturePlacesProvider.load_places",
        lambda _self: (),
    )

    result = reference_demand(
        south=0.0,
        west=0.0,
        north=0.01,
        east=0.01,
        origin_lon=0.0,
        origin_lat=0.0,
    )

    assert len(result["od"]) == 9
    assert len(result["layers"]) == 5
    assert {layer["purpose"] for layer in result["layers"]} == {
        "edu", "health", "shop", "air", "night"
    }
    assert all(len(layer["out"]) == 5 and len(layer["ret"]) == 5 for layer in result["layers"])
    assert all(len(row) == 4 for row in result["od"])
    assert result["meta"]["commuter_od_pairs"] == 9
    assert result["meta"]["purpose_layers"] == 5
    assert result["meta"]["baselineT_included"] is True
    assert len(result["baselineT"]) == 3
    assert all(-180.0 <= point[0] <= 180.0 and -90.0 <= point[1] <= 90.0 for point in result["pts"])


def test_reference_purpose_generator_uses_place_generators():
    from transit_planner.places import CityPlace
    from transit_planner.geo import Point
    from transit_planner.reference_demand import generate_reference_purpose_layer
    from transit_planner.reference_model import REFERENCE_PURPOSE_LAYERS

    zones = (
        DemandZone("z1", 0.0, 0.0, population=200.0),
        DemandZone("z2", 1500.0, 0.0, population=200.0),
        DemandZone("z3", 4000.0, 0.0, population=200.0),
    )
    shop = next(purpose for purpose in REFERENCE_PURPOSE_LAYERS if purpose.key == "shop")
    places = (
        CityPlace(
            "mall",
            "Mall",
            Point(0.0135, 0.0),
            basic_category="shopping_mall",
            importance=50.0,
        ),
    )

    result = generate_reference_purpose_layer(
        zones,
        places,
        purpose=shop,
        origin_lon=0.0,
        origin_lat=0.0,
    )

    assert result.od_pairs
    assert all(item[2] == int(item[2]) for item in result.od_pairs)
    assert all(item[3] >= 240.0 for item in result.od_pairs)
