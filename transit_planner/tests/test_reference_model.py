from transit_planner.city import DemandZone
from transit_planner.reference_demand import (
    build_daily_demand,
    build_demand_layers,
    generate_purpose_layer,
)
from transit_planner.reference_model import (
    CROWDED_LOAD_RATIO,
    EXTREME_LOAD_RATIO,
    REFERENCE_MODE_PROFILES,
    REFERENCE_CAR,
    REFERENCE_MOBILITY,
    REFERENCE_PERIODS,
    REFERENCE_TRANSFER,
    REFERENCE_PURPOSE_LAYERS,
    SEVERE_LOAD_RATIO,
)


def test_has_five_operating_periods():
    assert [(p.key, p.start_minute, p.end_minute) for p in REFERENCE_PERIODS] == [
        ("early", 240, 360),
        ("am", 360, 540),
        ("mid", 540, 900),
        ("pm", 900, 1140),
        ("eve", 1140, 1440),
    ]


def test_purpose_rules_have_five_outbound_and_return_shares():
    assert {rule.key for rule in REFERENCE_PURPOSE_LAYERS} == {
        "edu", "health", "shop", "air", "night"
    }
    for rule in REFERENCE_PURPOSE_LAYERS:
        assert len(rule.outbound_shares) == 5
        assert len(rule.return_shares) == 5
        assert abs(sum(rule.outbound_shares) - 1.0) < 1e-9
        assert abs(sum(rule.return_shares) - 1.0) < 1e-9


def test_mode_profiles_expose_capacity_dwell_and_row_cost():
    assert REFERENCE_MODE_PROFILES["bus"].capacity == 90
    assert REFERENCE_MODE_PROFILES["tram"].dwell_per_passenger_s == 0.6
    assert REFERENCE_MODE_PROFILES["metro"].rows["reserved"].cost_per_km == 32.0
    assert REFERENCE_MODE_PROFILES["rail"].platform_m == 140.0


def test_reference_generalized_cost_defaults():
    assert REFERENCE_TRANSFER.base_s == 405.0
    assert REFERENCE_CAR.cost_per_km_eur == 0.25
    assert REFERENCE_CAR.parking_s == 240.0
    assert REFERENCE_MOBILITY.two_wheel_speed_kph == 15.12
    assert REFERENCE_MOBILITY.two_wheel_reach_m == 7000.0


def test_crowding_thresholds_match_model_levels():
    assert (CROWDED_LOAD_RATIO, SEVERE_LOAD_RATIO, EXTREME_LOAD_RATIO) == (1.0, 2.0, 4.0)


def test_demand_layer_creates_both_directions_and_periods():
    zones = (
        DemandZone("o", 0, 0, population=1000),
        DemandZone("d", 1000, 0, purpose_attractions=(("shop", 100),)),
    )
    rule = next(rule for rule in REFERENCE_PURPOSE_LAYERS if rule.key == "shop")
    result = generate_purpose_layer(zones, purpose=rule)

    assert result.purpose == "shop"
    assert result.demand.by_period("am")
    assert any(pair.origin_zone_id == "o" and pair.destination_zone_id == "d" for pair in result.demand.pairs)
    assert any(pair.origin_zone_id == "d" and pair.destination_zone_id == "o" for pair in result.demand.pairs)


def test_demand_layers_and_daily_adapter_are_composable():
    zones = (
        DemandZone("o", 0, 0, population=1000),
        DemandZone("d", 1000, 0, purpose_attractions=(("night", 100),)),
    )
    layers = build_demand_layers(zones)
    daily = build_daily_demand(zones)

    assert len(layers.layers) == 5
    assert daily.total_trips_per_day > 0

def test_demand_layers_consume_place_purpose_aliases():
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
