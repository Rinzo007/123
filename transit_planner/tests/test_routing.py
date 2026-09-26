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


def test_router_respects_one_way_route() -> None:
    network = make_network()
    route = Route("one-way", "OW", TransitMode.BUS, ("a", "b"), both_ways=False)
    network.routes.clear()
    network.add_route(route)
    network.services.clear()
    network.add_service(Service("ow", "one-way", "bus", {"am": 10}))

    router = TransitRouter(network)
    assert router.shortest(network.stops["a"], network.stops["b"], period_id="am") is not None
    assert router.shortest(network.stops["b"], network.stops["a"], period_id="am") is None


def test_router_closes_circular_route() -> None:
    network = Network()
    for stop_id, x in (("a", 0), ("b", 1000), ("c", 2000)):
        network.add_stop(Stop(stop_id, stop_id.upper(), Point(x, 0)))
    network.add_vehicle_type(VehicleType("tram", "Tram", TransitMode.TRAM, 250))
    network.add_period(ServicePeriod("am", 360, 540))
    network.add_route(
        Route(
            "loop",
            "Loop",
            TransitMode.TRAM,
            ("a", "b", "c"),
            both_ways=False,
            closed=True,
        )
    )
    network.add_service(Service("loop-service", "loop", "tram", {"am": 10}))

    journey = TransitRouter(network).shortest(
        network.stops["c"],
        network.stops["a"],
        period_id="am",
    )
    assert journey is not None
    assert [(leg.from_id, leg.to_id) for leg in journey.legs if leg.kind == "transit"] == [
        ("c", "a")
    ]


def test_router_uses_physical_track_speed_limit() -> None:
    from transit_planner.infrastructure import TrackSection

    network = make_network()
    network.add_track_section(
        TrackSection(
            "slow",
            1.0,
            speed_limit_kph=10.0,
        )
    )
    network.routes.clear()
    network.add_route(
        Route(
            "slow-route",
            "Slow",
            TransitMode.BUS,
            ("a", "b"),
            track_section_ids=("slow",),
        )
    )
    network.services.clear()
    network.add_service(Service("slow-service", "slow-route", "bus", {"am": 10}))

    journey = TransitRouter(network).shortest(
        network.stops["a"],
        network.stops["b"],
        period_id="am",
    )
    assert journey is not None
    assert abs(journey.duration_min - 6.0) < 1e-9
