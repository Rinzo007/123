from transit_planner.network import TransitMode, default_vehicle_type


def test_default_vehicle_type_uses_main_mode_profile():
    bus = default_vehicle_type(TransitMode.BUS)
    tram = default_vehicle_type(TransitMode.TRAM)
    metro = default_vehicle_type(TransitMode.METRO)
    rail = default_vehicle_type(TransitMode.RAIL)

    assert bus.capacity == 90
    assert bus.operating_cost_per_km == 5.0
    assert tram.capacity == 250
    assert metro.capacity == 750
    assert rail.capacity == 1000
