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



def test_physical_track_topology():
    from transit_planner.infrastructure import (
        Crossover, SignalBlock, SignalDirection, TrackNode, TrackSection, TrackType,
    )
    node = TrackNode("n1", 10.0, 20.0, 4.0)
    section = TrackSection(
        "t1", 1.2, TrackType.TUNNEL,
        start_node_id="n1", start_elevation_m=4.0, end_elevation_m=10.0,
        max_slope_percent=1.0, curve_radius_m=250.0, track_count=2,
        direction=SignalDirection.FORWARD,
    )
    crossover = Crossover("x1", "t1", "t2", 0.5)
    block = SignalBlock("b1", "t1", 0.0, 1.0, SignalDirection.FORWARD, 75.0)
    assert node.elevation_m == 4.0
    assert section.track_type == TrackType.TUNNEL
    assert section.slope_percent == 0.5
    assert section.track_count == 2
    assert crossover.position == 0.5
    assert block.minimum_headway_seconds == 75.0
