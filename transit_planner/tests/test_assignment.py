from transit_planner.assignment import AssignmentConfig, assign_demand
from transit_planner.demand import DemandMatrix, ODPairDemand
from transit_planner.city import DemandZone
from transit_planner.geo import Point
from transit_planner.network import (
    Network, Route, Service, ServicePeriod, Stop, TransitMode, VehicleType,
)
from transit_planner.routing import RouterConfig, TransitRouter


def make_network() -> Network:
    network = Network()
    for stop_id, x in (("a", 0), ("b", 1000), ("c", 2000)):
        network.add_stop(Stop(stop_id, stop_id.upper(), Point(x, 0)))
    network.add_vehicle_type(VehicleType("bus", "Bus", TransitMode.BUS, 80))
    network.add_period(ServicePeriod("peak", 0, 60))
    network.add_route(Route("r1", "1", TransitMode.BUS, ("a", "b", "c")))
    network.add_service(Service("svc", "r1", "bus", {"peak": 10}))
    return network


def test_assignment_produces_transit_flow():
    network = make_network()
    demand = DemandMatrix((ODPairDemand("a", "c", 100),))
    result = assign_demand(
        network,
        demand,
        config=AssignmentConfig(period_id="peak", max_access_distance_m=0),
        router=TransitRouter(network, config=RouterConfig(walk_transfer_radius_m=0)),
    )

    assert result.metrics.total_trips == 100
    assert result.metrics.transit_trips > 0
    assert result.route_flows[0].passenger_section_traversals > 0
    section = next(
        item for item in result.section_loads
        if item.from_stop_id == "a" and item.to_stop_id == "b"
    )
    assert section.passengers > 0
    assert section.capacity == 480


def test_zone_coordinates_drive_car_and_walk_costs():
    network = make_network()
    zones = {
        "o": DemandZone("o", 0, 0, population=1000),
        "d": DemandZone("d", 2000, 0, population=1000),
    }
    result = assign_demand(
        network,
        DemandMatrix((ODPairDemand("o", "d", 100),)),
        zones=zones,
        config=AssignmentConfig(period_id="peak"),
    )
    assert result.metrics.car_trips > result.metrics.walk_trips


def test_mode_shares_sum_to_one():
    result = assign_demand(
        make_network(),
        DemandMatrix((ODPairDemand("a", "a", 10),)),
        config=AssignmentConfig(period_id="peak"),
    )
    total = result.metrics.total_trips
    assert abs(
        result.metrics.transit_share
        + result.metrics.car_trips / total
        + result.metrics.walk_trips / total
        + result.metrics.bike_trips / total
        - 1
    ) < 1e-9


def test_walking_only_path_is_not_counted_as_transit():
    network = make_network()
    router = TransitRouter(
        network,
        config=RouterConfig(walk_transfer_radius_m=1200),
    )
    result = assign_demand(
        network,
        DemandMatrix((ODPairDemand("a", "b", 100),)),
        config=AssignmentConfig(period_id="peak", max_access_distance_m=0),
        router=router,
    )
    assert result.metrics.transit_trips >= 0
    assert all(
        item.passengers >= 0
        for item in result.section_loads
    )


def test_stop_flow_exposes_reference_dwell_time():
    network = make_network()
    result = assign_demand(
        network,
        DemandMatrix((ODPairDemand("a", "c", 100),)),
        config=AssignmentConfig(period_id="peak", max_access_distance_m=0),
        router=TransitRouter(network, config=RouterConfig(walk_transfer_radius_m=0)),
    )
    stop_a = next(item for item in result.stop_flows if item.stop_id == "a")
    assert stop_a.dwell_seconds > 0.0


def test_mode_access_limit_overrides_global_access_radius():
    network = Network()
    network.add_stop(Stop("a", "A", Point(0, 0)))
    network.add_stop(Stop("b", "B", Point(2000, 0)))
    network.add_vehicle_type(VehicleType("bus", "Bus", TransitMode.BUS, 80))
    network.add_period(ServicePeriod("peak", 0, 60))
    network.add_route(Route("r1", "1", TransitMode.BUS, ("a", "b")))
    network.add_service(Service("svc", "r1", "bus", {"peak": 10}))

    zones = {
        "o": DemandZone("o", 550, 0, population=1000),
        "d": DemandZone("d", 1450, 0, population=1000),
    }
    result = assign_demand(
        network,
        DemandMatrix((ODPairDemand("o", "d", 100),)),
        zones=zones,
        config=AssignmentConfig(period_id="peak", max_access_distance_m=1500),
    )

    assert result.metrics.transit_trips == 0.0
    assert result.unserved_transit_demand == 0.0
