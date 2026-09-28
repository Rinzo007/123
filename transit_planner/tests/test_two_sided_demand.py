from transit_planner.city import DemandZone
from transit_planner.city_demand import build_city_temporal_demand, build_city_temporal_demand_two_sided


def make_zones() -> tuple[DemandZone, ...]:
    return (
        DemandZone("z1", 0.0, 0.0, population=1000.0, jobs=100.0),
        DemandZone("z2", 5000.0, 0.0, population=100.0, jobs=1000.0),
    )


def test_two_sided_demand_preserves_total_trips() -> None:
    zones = make_zones()
    base = build_city_temporal_demand(zones)
    two_sided = build_city_temporal_demand_two_sided(zones)

    assert two_sided.total_trips == base.total_trips


def test_two_sided_demand_shifts_volume_to_dominant_direction() -> None:
    zones = make_zones()
    base = build_city_temporal_demand(zones)
    two_sided = build_city_temporal_demand_two_sided(zones)

    base_am = sum(pair.trips for pair in base.by_period("am"))
    two_sided_am = sum(pair.trips for pair in two_sided.by_period("am"))
    base_pm = sum(pair.trips for pair in base.by_period("pm"))
    two_sided_pm = sum(pair.trips for pair in two_sided.by_period("pm"))

    # AM outbound dominates (0.6 vs 0.06), PM return dominates (0.55 vs 0.1):
    # two-sided must raise AM and PM relative to the averaged shares.
    assert two_sided_am > base_am
    assert two_sided_pm > base_pm
