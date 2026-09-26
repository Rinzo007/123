from transit_planner.demand import DemandMatrix, ODPairDemand
from transit_planner.geo import Point
from transit_planner.network import (
    Network, Route, Service, ServicePeriod, Stop, TransitMode, VehicleType,
)
from transit_planner.simulation import calculate_service_capacity, simulate_capacity


def make_network() -> Network:
    network = Network()
    network.add_stop(Stop("a", "A", Point(0, 0)))
    network.add_stop(Stop("b", "B", Point(1, 0)))
    network.add_vehicle_type(VehicleType("bus", "Bus", TransitMode.BUS, 80))
    network.add_period(ServicePeriod("peak", 360, 540))
    network.add_route(Route("r1", "1", TransitMode.BUS, ("a", "b")))
    network.add_service(Service("svc", "r1", "bus", {"peak": 10}))
    return network


def test_capacity_calculation():
    result = calculate_service_capacity(make_network(), "svc", "peak")
    assert result.departures == 18
    assert result.offered_capacity == 1440


def test_capacity_and_demand_are_exposed():
    demand = DemandMatrix((ODPairDemand("z1", "z2", 1200),))
    result = simulate_capacity(make_network(), demand)
    assert result.total_demand_trips_per_day == 1200
    assert result.total_transit_supply == 1440
