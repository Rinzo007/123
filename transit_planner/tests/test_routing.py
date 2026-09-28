import pytest

from transit_planner.geo import Point
from transit_planner.network import Network, Route, Service, ServicePeriod, Stop, TransitMode, VehicleType
from transit_planner.road import RoadEdge, RoadGraph, RoadNode
from transit_planner.routing import RouterConfig, TransitRouter


def make_network() -> Network:
    network = Network()
    network.add_stop(Stop("a", "A", Point(0, 0)))
    network.add_stop(Stop("b", "B", Point(1000, 0)))
    network.add_stop(Stop("c", "C", Point(2000, 0)))
    network.add_vehicle_type(VehicleType("bus", "Bus", TransitMode.BUS, 90))
    network.add_period(ServicePeriod("am", 360, 540))
    network.add_route(Route("r1", "1", TransitMode.BUS, ("a", "b", "c")))
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

def test_router_uses_physical_track_length():
    from transit_planner.infrastructure import TrackSection

    network = make_network()
    network.add_track_section(
        TrackSection(
            "physical",
            2.0,
            speed_limit_kph=10.0,
        )
    )
    network.routes.clear()
    network.add_route(
        Route(
            "physical-route",
            "Physical",
            TransitMode.BUS,
            ("a", "b"),
            track_section_ids=("physical",),
        )
    )
    network.services.clear()
    network.add_service(Service("physical-service", "physical-route", "bus", {"am": 10}))

    journey = TransitRouter(network).shortest(
        network.stops["a"],
        network.stops["b"],
        period_id="am",
    )
    assert journey is not None
    assert abs(journey.duration_min - 12.0) < 1e-9


def test_router_applies_reference_transfer_penalty_and_multiplier():
    network = Network()
    for stop_id, x in (("a", 0), ("b", 1000), ("c", 2000)):
        network.add_stop(Stop(stop_id, stop_id.upper(), Point(x, 0)))
    network.add_vehicle_type(VehicleType("bus", "Bus", TransitMode.BUS, 90))
    network.add_period(ServicePeriod("am", 360, 540))
    network.add_route(Route("r1", "1", TransitMode.BUS, ("a", "b")))
    network.add_route(Route("r2", "2", TransitMode.BUS, ("b", "c")))
    network.add_service(Service("s1", "r1", "bus", {"am": 10}))
    network.add_service(Service("s2", "r2", "bus", {"am": 10}))

    journey = TransitRouter(network).shortest(network.stops["a"], network.stops["c"], period_id="am")

    assert journey is not None
    assert journey.transfers == 1
    assert any(leg.kind == "transit" and leg.wait_min >= 0 for leg in journey.legs)


def test_router_can_return_route_diverse_alternatives():
    network = Network()
    for stop_id, x, y in (("a", 0, 0), ("b", 1000, 0), ("c", 1000, 1000), ("d", 2000, 0)):
        network.add_stop(Stop(stop_id, stop_id.upper(), Point(x, y)))
    network.add_vehicle_type(VehicleType("bus", "Bus", TransitMode.BUS, 90))
    network.add_period(ServicePeriod("am", 360, 540))
    network.add_route(Route("direct", "Direct", TransitMode.BUS, ("a", "b", "d")))
    network.add_route(Route("detour", "Detour", TransitMode.BUS, ("a", "c", "d")))
    network.add_service(Service("direct-service", "direct", "bus", {"am": 10}))
    network.add_service(Service("detour-service", "detour", "bus", {"am": 10}))

    alternatives = TransitRouter(network).shortest_alternatives(
        network.stops["a"],
        network.stops["d"],
        period_id="am",
        max_alternatives=2,
    )

    assert len(alternatives) == 2
    sequences = [
        tuple(leg.route_id for leg in journey.legs if leg.kind == "transit")
        for journey in alternatives
    ]
    assert sequences[0] != sequences[1]


def test_router_applies_service_headway_feedback_factor():
    network = Network()
    network.add_stop(Stop("a", "A", Point(0, 0)))
    network.add_stop(Stop("b", "B", Point(1000, 0)))
    network.add_vehicle_type(VehicleType("bus", "Bus", TransitMode.BUS, 90))
    network.add_period(ServicePeriod("peak", 0, 60))
    network.add_route(Route("r1", "1", TransitMode.BUS, ("a", "b")))
    network.add_service(Service("svc", "r1", "bus", {"peak": 10}))

    router = TransitRouter(
        network,
        config=RouterConfig(walk_transfer_radius_m=0),
    )
    base = router.shortest(
        network.stops["a"],
        network.stops["b"],
        period_id="peak",
    )
    slowed = router.shortest(
        network.stops["a"],
        network.stops["b"],
        period_id="peak",
        service_headway_factors={"svc": 2.0},
    )

    assert base is not None and slowed is not None
    assert slowed.legs[0].wait_min == 10.0
    assert base.legs[0].wait_min == 5.0


def test_router_uses_route_geometry_curve_runtime():
    network = Network()
    network.add_stop(Stop("a", "A", Point(0.0, 0.0)))
    network.add_stop(Stop("b", "B", Point(20.0, 0.0)))
    network.add_vehicle_type(VehicleType("bus", "Bus", TransitMode.BUS, 90))
    network.add_period(ServicePeriod("am", 360, 540))
    network.add_route(
        Route(
            "curve",
            "Curve",
            TransitMode.BUS,
            ("a", "b"),
            geometry=__import__("transit_planner.geo", fromlist=["LineString"]).LineString(
                (
                    Point(0.0, 0.0),
                    Point(10.0, 10.0),
                    Point(20.0, 0.0),
                )
            ),
        )
    )
    network.add_service(Service("curve-service", "curve", "bus", {"am": 10}))

    router = TransitRouter(network, config=RouterConfig(walk_transfer_radius_m=0))
    journey = router.shortest(
        network.stops["a"],
        network.stops["b"],
        period_id="am",
    )

    assert journey is not None
    assert journey.duration_min > network.route_length_km(network.routes["curve"]) / 18.0 * 60.0


def test_router_includes_intermediate_dwell_once():
    network = make_network()
    router = TransitRouter(network, config=RouterConfig(walk_transfer_radius_m=0))
    journey = router.shortest(
        network.stops["a"],
        network.stops["c"],
        period_id="am",
    )

    assert journey is not None
    run_only = (
        network.route_segment_run_time_min(network.routes["r1"], 0)
        + network.route_segment_run_time_min(network.routes["r1"], 1)
    )
    expected = run_only + 20.0 / 60.0
    assert abs(journey.duration_min - expected) < 1e-9


def test_router_applies_segment_crowding_penalty_to_runtime():
    network = make_network()
    router = TransitRouter(network, config=RouterConfig(walk_transfer_radius_m=0))
    base = router.shortest(
        network.stops["a"],
        network.stops["c"],
        period_id="am",
    )
    crowded = router.shortest(
        network.stops["a"],
        network.stops["c"],
        period_id="am",
        segment_crowding_penalties={("r1", "b", "c"): 5.0},
    )

    assert base is not None and crowded is not None
    assert abs(crowded.duration_min - base.duration_min - 5.0) < 1e-9


def test_router_allows_through_running_stop_but_not_boarding_at_closed_stop():
    network = Network()
    for stop_id, x in (("a", 0), ("b", 1000), ("c", 2000)):
        network.add_stop(Stop(stop_id, stop_id.upper(), Point(x, 0)))
    network.add_vehicle_type(VehicleType("bus", "Bus", TransitMode.BUS, 90))
    network.add_period(ServicePeriod("am", 360, 540))
    network.add_route(
        Route(
            "r1",
            "1",
            TransitMode.BUS,
            ("a", "b", "c"),
            open_stop_ids=("a", "c"),
        )
    )
    network.add_service(Service("svc", "r1", "bus", {"am": 10}))
    router = TransitRouter(network, config=RouterConfig(walk_transfer_radius_m=0))

    through = router.shortest(
        network.stops["a"],
        network.stops["c"],
        period_id="am",
    )
    closed_origin = router.shortest(
        network.stops["b"],
        network.stops["c"],
        period_id="am",
    )

    assert through is not None
    assert closed_origin is None


def make_street_network() -> tuple[Network, RoadGraph]:
    network = Network()
    for stop_id, x in (("a", 0), ("b", 1000), ("c", 1600)):
        network.add_stop(Stop(stop_id, stop_id.upper(), Point(x, 0)))
    network.add_vehicle_type(VehicleType("bus", "Bus", TransitMode.BUS, 90))
    network.add_period(ServicePeriod("am", 360, 540))
    network.add_route(Route("r1", "1", TransitMode.BUS, ("a", "b", "c")))
    network.add_service(Service("svc", "r1", "bus", {"am": 10}))

    graph = RoadGraph()
    for node_id, x in ((1, 0), (2, 1000), (3, 1600), (10, 500)):
        graph.add_node(RoadNode(node_id, float(x), 0.0))
    graph.add_edge(RoadEdge("aq", 1, 10, 500.0, 30.0, "residential"))
    graph.add_edge(RoadEdge("qb", 10, 2, 500.0, 30.0, "residential"))
    graph.add_edge(RoadEdge("bc", 2, 3, 600.0, 30.0, "residential"))
    return network, graph


def test_door_to_door_searches_every_stop_within_walking_catchment():
    network, graph = make_street_network()
    router = TransitRouter(network, road_graph=graph)

    journey = router.shortest_from_points(
        Point(500.0, 0.0),
        Point(1600.0, 0.0),
        origin_id="door",
        destination_id="hall",
        period_id="am",
    )

    assert journey is not None
    # A and B are equally far on foot, but boarding at B saves a transit
    # segment. The search must consider B, not only the tied nearest stop.
    assert journey.origin_stop_id == "b"
    assert journey.destination_stop_id == "c"
    assert journey.legs[0].kind == "access"
    assert (journey.legs[0].from_id, journey.legs[0].to_id) == ("door", "b")
    assert journey.legs[0].duration_min == pytest.approx(6.0)
    assert journey.legs[-1].kind == "egress"
    assert (journey.legs[-1].from_id, journey.legs[-1].to_id) == ("c", "hall")
    assert journey.legs[-1].duration_min == 0.0
    assert journey.duration_min == pytest.approx(
        journey.legs[0].duration_min
        + network.route_segment_run_time_min(network.routes["r1"], 1)
    )


def test_door_to_door_without_street_graph_is_an_error():
    router = TransitRouter(make_network())

    with pytest.raises(RuntimeError, match="road_graph"):
        router.shortest_from_points(
            Point(0.0, 0.0),
            Point(1000.0, 0.0),
            period_id="am",
        )


def test_door_to_door_rejects_a_point_outside_street_snap_range():
    network, graph = make_street_network()
    router = TransitRouter(network, road_graph=graph)

    with pytest.raises(ValueError, match="farther than"):
        router.shortest_from_points(
            Point(5000.0, 0.0),
            Point(1600.0, 0.0),
            period_id="am",
        )
