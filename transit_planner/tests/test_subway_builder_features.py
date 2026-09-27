from transit_planner.blueprint import AssetState, BlueprintProject, TrackBlueprint
from transit_planner.fares import FareGroup, FareSystem, TransferPolicy
from transit_planner.infrastructure import TrackSection
from transit_planner.rolling_stock import RollingStockType
from transit_planner.stations import Platform, PlatformLayout, Station, StationGroup


def test_station_platform_and_group():
    platform = Platform("p1", "s1", 120.0, ("t1",), PlatformLayout.ISLAND)
    station = Station("s1", "Central", "stop-1", ("p1",), interchange=True, platform_length_m=120)
    group = StationGroup("g1", "Central interchange", ("s1", "s2"), transfer_walk_min=3)
    assert platform.layout == PlatformLayout.ISLAND
    assert station.interchange
    assert group.transfer_walk_min == 3


def test_rolling_stock_physics_and_capacity():
    train = RollingStockType(
        "metro-8", "8-car metro", "metro", 180, 20, 3.0, 8,
        90, 1.0, 1.2, 1.0, 180, 25,
        car_cost=1_000_000, train_operating_cost_per_hour=100,
        car_operating_cost_per_hour=10, track_maintenance_cost_per_km_year=1000,
        station_maintenance_cost_per_year=5000, tph_limit=30,
    )
    assert train.capacity == 1440
    assert train.train_length_m == 160


def test_fare_systems_and_rounding():
    distance = FareGroup(
        "d", "Distance", FareSystem.DISTANCE,
        boarding_charge=1.37, per_km_rate=0.17, fare_cap=6
    )
    assert distance.price(distance_km=10) == 3.05
    assert distance.price(distance_km=100) == 6
    free = FareGroup("f", "Free transfers", transfer_policy=TransferPolicy.FREE)
    assert free.price(transfer=True) == 0


def test_blueprint_lifecycle():
    project = BlueprintProject("p", "Line A")
    project.add(TrackBlueprint("b", (TrackSection("t", 1.0),)))
    assert project.build_all() == ("b",)
    assert project.activate_all() == ("b",)
    assert project.blueprints["b"].state == AssetState.ACTIVE
