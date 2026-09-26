from transit_planner.assignment import AssignmentConfig, assign_demand
from transit_planner.city import DemandZone
from transit_planner.demand import DemandMatrix, ODPairDemand
from transit_planner.geo import Point
from transit_planner.network import (
    Network,
    Route,
    Service,
    ServicePeriod,
    Stop,
    TransitMode,
    VehicleType,
)


def build_network() -> Network:
    network = Network()
    network.add_stop(Stop("a", "A", Point(0, 0)))
    network.add_stop(Stop("b", "B", Point(1000, 0)))
    network.add_vehicle_type(VehicleType("metro", "Metro", TransitMode.METRO, 750))
    network.add_period(ServicePeriod("am", 360, 540))
    network.add_route(Route("r1", "M1", TransitMode.METRO, ("a", "b")))
    network.add_service(Service("svc", "r1", "metro", {"am": 1}))
    return network


def test_track_capacity_limits_one_minute_metro_headway():
    network = build_network()
    demand = DemandMatrix((ODPairDemand("a", "b", 1000.0),))

    result = assign_demand(
        network,
        demand,
        config=AssignmentConfig(period_id="am", max_access_distance_m=0),
    )

    section = next(item for item in result.section_loads if item.from_stop_id == "a")
    # 3 hours * 30 tph = 90 departures per direction.
    assert section.capacity == 90 * 750


def test_stop_dwell_uses_reference_base_and_passenger_component():
    network = build_network()
    demand = DemandMatrix((ODPairDemand("a", "b", 100.0),))

    result = assign_demand(
        network,
        demand,
        config=AssignmentConfig(period_id="am", max_access_distance_m=0),
    )

    stop = next(item for item in result.stop_flows if item.stop_id == "a")
    assert stop.dwell_seconds >= 90 * 30.0
    assert stop.platform_m == 100.0
