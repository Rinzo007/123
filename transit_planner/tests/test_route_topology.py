from transit_planner.network import Network, Route, Stop, TransitMode
from transit_planner.geo import Point
from transit_planner.serialization import dumps_network, loads_network


def test_one_way_route_does_not_create_reverse_segment():
    network = Network()
    network.add_stop(Stop("a", "A", Point(0, 0)))
    network.add_stop(Stop("b", "B", Point(1000, 0)))
    network.add_route(Route("r1", "1", TransitMode.BUS, ("a", "b"), both_ways=False))

    assert network.routes["r1"].both_ways is False
    assert network.routes["r1"].segment_pairs() == (("a", "b"),)


def test_closed_route_has_closing_segment_and_round_trips():
    network = Network()
    network.add_stop(Stop("a", "A", Point(0, 0)))
    network.add_stop(Stop("b", "B", Point(1000, 0)))
    network.add_stop(Stop("c", "C", Point(500, 1000)))
    network.add_route(
        Route(
            "r1",
            "Loop",
            TransitMode.BUS,
            ("a", "b", "c"),
            both_ways=False,
            closed=True,
        )
    )

    assert network.routes["r1"].segment_pairs()[-1] == ("c", "a")
    restored = loads_network(dumps_network(network))
    route = restored.routes["r1"]
    assert route.closed is True
    assert route.both_ways is False
    assert route.segment_pairs()[-1] == ("c", "a")
