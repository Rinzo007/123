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
