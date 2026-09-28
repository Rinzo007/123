import pytest

from transit_planner.geo import Point
from transit_planner.road import RoadEdge, RoadGraph, RoadNode


def make_graph() -> RoadGraph:
    graph = RoadGraph()
    graph.add_node(RoadNode(1, 0.0, 0.0))
    graph.add_node(RoadNode(2, 600.0, 0.0))
    graph.add_node(RoadNode(3, 1200.0, 0.0))
    graph.add_edge(RoadEdge("walk", 1, 2, 600.0, 30.0, "residential"))
    graph.add_edge(RoadEdge("motorway", 2, 3, 600.0, 100.0, "motorway"))
    return graph


def test_walking_search_ignores_direction_but_not_motorways():
    search = make_graph().walking_search(2, walking_speed_kph=5.0)

    assert search.minutes[1] == pytest.approx(7.2)
    assert search.minutes[2] == 0.0
    assert 3 not in search.minutes


def test_walking_path_reports_traversal_direction():
    graph = make_graph()

    forward = graph.walking_search(1, walking_speed_kph=5.0)
    assert graph.walk_path_edges(forward, 2, root_first=True) == (("walk", True),)

    reverse = graph.walking_search(2, walking_speed_kph=5.0)
    assert graph.walk_path_edges(reverse, 1, root_first=False) == (("walk", False),)


def test_walking_path_geometry_follows_traversal_direction():
    graph = RoadGraph()
    graph.add_node(RoadNode(1, 0.0, 0.0))
    graph.add_node(RoadNode(2, 600.0, 0.0))
    graph.add_edge(
        RoadEdge(
            "walk",
            1,
            2,
            600.0,
            30.0,
            "residential",
            geometry=(Point(0.0, 0.0), Point(300.0, 50.0), Point(600.0, 0.0)),
        )
    )

    search = graph.walking_search(2, walking_speed_kph=5.0)
    oriented = graph.walk_path_edges(search, 1, root_first=False)

    assert oriented == (("walk", False),)
    assert graph.walk_path_geometry(oriented) == (
        Point(0.0, 0.0),
        Point(300.0, 50.0),
        Point(600.0, 0.0),
    )


def test_walking_search_rejects_bad_inputs():
    graph = make_graph()

    with pytest.raises(KeyError):
        graph.walking_search(99, walking_speed_kph=5.0)
    with pytest.raises(ValueError):
        graph.walking_search(1, walking_speed_kph=0.0)
