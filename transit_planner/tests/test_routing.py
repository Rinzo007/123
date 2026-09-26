from transit_planner.geo import Point
from transit_planner.network import Network, Route, Service, ServicePeriod, Stop, TransitMode, VehicleType
from transit_planner.routing import TransitRouter


def make_network() -> Network:
    network = Network()
    network.add_stop(Stop("a", "A", Point(0, 0)))
    network.add_stop(Stop("b", "B", Point(1000, 0)))
    network.add_vehicle_type(VehicleType("bus", "Bus", TransitMode.BUS, 90))
    network.add_period(ServicePeriod("am", 360, 540))
    network.add_route(Route("r1", "1", TransitMode.BUS, ("a", "b")))
    network.add_service(Service("svc", "r1", "bus", {"am": 10}))
    return network


def test_router_can_run_in_both_directions():
    network = make_network()
    router = TransitRouter(network)

    forward = router.shortest(
        network.stops["a"],
        network.stops["b"],
        period_id="am",
    )
    reverse = router.shortest(
        network.stops["b"],
        network.stops["a"],
        period_id="am",
    )

    assert forward is not None
    assert reverse is not None
    assert [leg.route_id for leg in forward.legs if leg.kind == "transit"] == ["r1"]
    assert [leg.route_id for leg in reverse.legs if leg.kind == "transit"] == ["r1"]


def test_router_uses_metric_geometry_for_run_time():
    network = make_network()
    router = TransitRouter(network)

    journey = router.shortest(
        network.stops["a"],
        network.stops["b"],
        period_id="am",
    )

    assert journey is not None
    assert journey.duration_min >= 3.0
    assert journey.duration_min < 6.0
    transit_legs = [leg for leg in journey.legs if leg.kind == "transit"]
    assert transit_legs
    assert 0.0 <= transit_legs[0].wait_min < 10.0
