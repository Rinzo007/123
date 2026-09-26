from transit_planner.analytics import analyze_network, calculate_accessibility
from transit_planner.assignment import AssignmentConfig, assign_demand
from transit_planner.city import DemandZone
from transit_planner.demand import DemandMatrix, ODPairDemand
from transit_planner.geo import Point
from transit_planner.network import (
    Network, Route, Service, ServicePeriod, Stop, TransitMode, VehicleType,
)


def make_network() -> Network:
    network = Network()
    for stop_id, x in (("a", 0), ("b", 1000), ("c", 2000)):
        network.add_stop(Stop(stop_id, stop_id.upper(), Point(x, 0)))
    network.add_vehicle_type(VehicleType("bus", "Bus", TransitMode.BUS, 80, 2.0))
    network.add_period(ServicePeriod("peak", 0, 60))
    network.add_route(Route("r1", "1", TransitMode.BUS, ("a", "b", "c")))
    network.add_service(Service("svc", "r1", "bus", {"peak": 10}))
    return network


def test_network_analytics_exposes_stop_and_section_metrics():
    network = make_network()
    zones = (
        DemandZone("a", 0, 0, population=1000, jobs=500),
        DemandZone("c", 2000, 0, population=500, jobs=1000),
    )
    assignment = assign_demand(
        network,
        DemandMatrix((ODPairDemand("a", "c", 100),)),
        zones={z.id: z for z in zones},
        config=AssignmentConfig(period_id="peak"),
    )
    result = analyze_network(network, assignment, zones=zones)
    assert len(result.stops) == 3
    assert result.passenger_km > 0
    assert result.accessibility[0].population_covered == 1500


def test_accessibility_respects_radius():
    network = make_network()
    zones = (DemandZone("far", 10000, 0, population=100),)
    result = calculate_accessibility(network, zones, radius_m=500)
    assert result.population_share == 0.0

def test_one_way_service_analytics_uses_one_direction_of_vehicle_km():
    network = Network()
    network.add_stop(Stop("a", "A", Point(0, 0)))
    network.add_stop(Stop("b", "B", Point(1000, 0)))
    network.add_vehicle_type(VehicleType("bus", "Bus", TransitMode.BUS, 80, 2.0))
    network.add_period(ServicePeriod("peak", 0, 60))
    network.add_route(
        Route("r1", "1", TransitMode.BUS, ("a", "b"), both_ways=False)
    )
    network.add_service(Service("svc", "r1", "bus", {"peak": 10}))

    assignment = assign_demand(
        network,
        DemandMatrix((ODPairDemand("a", "b", 1),)),
        config=AssignmentConfig(period_id="peak", max_access_distance_m=0),
    )
    result = analyze_network(network, assignment)

    service = result.services[0]
    assert service.departures == 6
    assert service.daily_vehicle_km == 6.0
    assert service.daily_opex == 12.0

def test_analytics_uses_physical_segment_length_for_passenger_km():
    from transit_planner.infrastructure import TrackSection

    network = Network()
    network.add_stop(Stop("a", "A", Point(0, 0)))
    network.add_stop(Stop("b", "B", Point(1000, 0)))
    network.add_track_section(TrackSection("physical", 3.0))
    network.add_vehicle_type(VehicleType("bus", "Bus", TransitMode.BUS, 80))
    network.add_period(ServicePeriod("peak", 0, 60))
    network.add_route(
        Route(
            "r1",
            "1",
            TransitMode.BUS,
            ("a", "b"),
            track_section_ids=("physical",),
        )
    )
    network.add_service(Service("svc", "r1", "bus", {"peak": 10}))

    assignment = assign_demand(
        network,
        DemandMatrix((ODPairDemand("a", "b", 10),)),
        config=AssignmentConfig(period_id="peak", max_access_distance_m=0),
    )
    result = analyze_network(network, assignment)

    forward = next(section for section in result.sections if section.from_stop_id == "a")
    assert forward.distance_km == 3.0
    assert result.passenger_km == forward.passengers * 3.0

def test_analytics_keeps_distinct_closed_reverse_segments():
    from transit_planner.infrastructure import TrackSection

    network = Network()
    network.add_stop(Stop("a", "A", Point(0, 0)))
    network.add_stop(Stop("b", "B", Point(1000, 0)))
    network.add_track_section(TrackSection("ab", 1.0))
    network.add_track_section(TrackSection("ba", 3.0))
    network.add_vehicle_type(VehicleType("tram", "Tram", TransitMode.TRAM, 250))
    network.add_period(ServicePeriod("peak", 0, 60))
    network.add_route(
        Route(
            "loop",
            "Loop",
            TransitMode.TRAM,
            ("a", "b"),
            track_section_ids=("ab", "ba"),
            closed=True,
        )
    )
    network.add_service(Service("svc", "loop", "tram", {"peak": 10}))

    assignment = assign_demand(
        network,
        DemandMatrix(
            (
                ODPairDemand("a", "b", 10),
                ODPairDemand("b", "a", 10),
            )
        ),
        config=AssignmentConfig(period_id="peak", max_access_distance_m=0),
    )
    result = analyze_network(network, assignment)

    forward = next(section for section in result.sections if section.from_stop_id == "a")
    reverse = next(section for section in result.sections if section.from_stop_id == "b")
    assert forward.distance_km == 1.0
    assert reverse.distance_km == 3.0
