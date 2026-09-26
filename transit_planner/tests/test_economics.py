from transit_planner.assignment import AssignmentConfig, assign_demand
from transit_planner.demand import DemandMatrix, ODPairDemand
from transit_planner.economics import EconomicsConfig, calculate_economics
from transit_planner.infrastructure import TrackSection, TrackType
from transit_planner.geo import Point
from transit_planner.network import (
    Network, Route, Service, ServicePeriod, Stop, TransitMode, VehicleType,
)


def test_operating_economics():
    network = Network()
    network.add_stop(Stop("a", "A", Point(0, 0)))
    network.add_stop(Stop("b", "B", Point(1000, 0)))
    network.add_vehicle_type(VehicleType("bus", "Bus", TransitMode.BUS, 80, 2.0))
    network.add_period(ServicePeriod("peak", 0, 60))
    network.add_route(Route("r1", "1", TransitMode.BUS, ("a", "b")))
    network.add_service(Service("svc", "r1", "bus", {"peak": 10}))

    assignment = assign_demand(
        network,
        DemandMatrix((ODPairDemand("a", "b", 100),)),
        config=AssignmentConfig(period_id="peak", max_access_distance_m=0),
    )
    result = calculate_economics(
        network,
        assignment,
        config=EconomicsConfig(period_id="peak", fare_per_transit_trip=2.0),
    )
    assert result.daily_vehicle_km == 12.0
    assert result.daily_fare_revenue > 0
    assert result.annual_operating_cost == result.daily_operating_cost * 365


def test_reference_fleet_cost_is_calculated():
    network = Network()
    network.add_stop(Stop("a", "A", Point(0, 0)))
    network.add_stop(Stop("b", "B", Point(1000, 0)))
    network.add_vehicle_type(VehicleType("bus", "Bus", TransitMode.BUS, 80))
    network.add_period(ServicePeriod("peak", 0, 60))
    network.add_route(Route("r1", "1", TransitMode.BUS, ("a", "b")))
    network.add_service(Service("svc", "r1", "bus", {"peak": 10}))

    assignment = assign_demand(
        network,
        DemandMatrix((ODPairDemand("a", "b", 10),)),
        config=AssignmentConfig(period_id="peak", max_access_distance_m=0),
    )
    result = calculate_economics(
        network,
        assignment,
        config=EconomicsConfig(period_id="peak"),
    )

    assert result.daily_fleet_cost == 250.0
    assert result.annual_fleet_cost == 250.0 * 365


def test_linked_track_sections_drive_capital_cost_by_track_type():
    network = Network()
    network.add_stop(Stop("a", "A", Point(0, 0), True))
    network.add_stop(Stop("b", "B", Point(1000, 0), True))
    network.add_track_section(TrackSection("surface", 0.75, TrackType.SURFACE))
    network.add_track_section(TrackSection("tunnel", 1.25, TrackType.TUNNEL))
    network.add_vehicle_type(VehicleType("metro", "Metro", TransitMode.METRO, 750))
    network.add_period(ServicePeriod("peak", 0, 60))
    network.add_route(
        Route(
            "r1",
            "1",
            TransitMode.METRO,
            ("a", "b"),
            track_section_ids=("tunnel",),
        )
    )
    network.add_service(Service("svc", "r1", "metro", {"peak": 10}))

    assignment = assign_demand(
        network,
        DemandMatrix((ODPairDemand("a", "b", 10),)),
        config=AssignmentConfig(period_id="peak", max_access_distance_m=0),
    )
    result = calculate_economics(
        network,
        assignment,
        config=EconomicsConfig(
            period_id="peak",
            infrastructure_cost_per_km={TransitMode.METRO: 1.0},
            infrastructure_cost_per_track_km={
                TrackType.SURFACE: 2.0,
                TrackType.TUNNEL: 10.0,
            },
            station_cost=3.0,
        ),
    )

    assert result.capital_cost == 1.25 * 10.0 + 2 * 3.0


def test_route_capital_cost_is_not_duplicated_across_services() -> None:
    network = Network()
    network.add_stop(Stop("a", "A", Point(0, 0)))
    network.add_stop(Stop("b", "B", Point(1000, 0)))
    network.add_track_section(TrackSection("s1", 1.0, TrackType.SURFACE))
    network.add_vehicle_type(VehicleType("tram", "Tram", TransitMode.TRAM, 250))
    network.add_period(ServicePeriod("am", 360, 540))
    network.add_route(
        Route(
            "r1",
            "1",
            TransitMode.TRAM,
            ("a", "b"),
            track_section_ids=("s1",),
        )
    )
    network.add_service(Service("svc-am-1", "r1", "tram", {"am": 10}))
    network.add_service(Service("svc-am-2", "r1", "tram", {"am": 15}))

    assignment = assign_demand(
        network,
        DemandMatrix((ODPairDemand("a", "b", 1.0),)),
        config=AssignmentConfig(period_id="am", max_access_distance_m=0),
    )
    result = calculate_economics(
        network,
        assignment,
        config=EconomicsConfig(
            period_id="am",
            infrastructure_cost_per_track_km={TrackType.SURFACE: 7.0},
        ),
    )

    assert result.capital_cost == 7.0
