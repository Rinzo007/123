from transit_planner.data import ConnectorRef, GeoJSONRoadProvider, RoadRecord
from transit_planner.geo import LineString, Point
from transit_planner.road_builder import build_road_graph, build_topological_road_graph
from transit_planner.spatial import GridPointIndex, IndexedPoint


def test_one_way_graph_and_shortest_path():
    roads = GeoJSONRoadProvider(
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": "ab",
                    "properties": {"speed_kph": 30, "highway": "residential", "oneway": True},
                    "geometry": {"type": "LineString", "coordinates": [[0, 0], [1000, 0]]},
                },
                {
                    "type": "Feature",
                    "id": "bc",
                    "properties": {"speed_kph": 30, "highway": "residential", "oneway": False},
                    "geometry": {"type": "LineString", "coordinates": [[1000, 0], [1000, 1000]]},
                },
            ],
        }
    ).load_roads()

    result = build_road_graph(roads)
    graph = result.graph

    assert result.node_count == 3
    assert result.edge_count == 3

    start = next(n.id for n in graph.nodes.values() if n.x == 0 and n.y == 0)
    end = next(n.id for n in graph.nodes.values() if n.x == 1000 and n.y == 1000)

    time, path = graph.shortest_path(start, end)
    assert round(time, 2) == 4.0
    assert path == ("ab", "bc")

    reverse_time, reverse_path = graph.shortest_path(end, start)
    assert reverse_time == float("inf")
    assert reverse_path == ()


def test_grid_index_nearest():
    index = GridPointIndex(cell_size=10)
    index.insert(IndexedPoint(1, 5, 5))
    index.insert(IndexedPoint(2, 25, 5))

    assert index.nearest(7, 6).id == 1
    assert index.nearest(7, 6, max_radius=1) is None


def test_topological_graph_uses_shared_connectors_not_coincident_coordinates():
    roads = (
        RoadRecord(
            "a",
            LineString((
                Point(0, 0),
                Point(1, 0),
            )),
            30,
            connectors=(ConnectorRef("c0", 0.0), ConnectorRef("c1", 1.0)),
            length_m=100.0,
        ),
        RoadRecord(
            "b",
            LineString((
                Point(1, 0),
                Point(2, 0),
            )),
            30,
            connectors=(ConnectorRef("c1", 0.0), ConnectorRef("c2", 1.0)),
            length_m=200.0,
        ),
    )

    result = build_topological_road_graph(roads)
    graph = result.graph
    start = graph.connector_nodes["c0"]
    end = graph.connector_nodes["c2"]

    travel_time, path = graph.shortest_path(start, end)
    assert round(travel_time, 2) == 0.6
    assert path == ("a:c0:c1", "b:c1:c2")


def test_topological_graph_does_not_connect_by_coordinate_alone():
    roads = (
        RoadRecord(
            "a",
            LineString((Point(0, 0), Point(1, 0))),
            30,
            connectors=(ConnectorRef("a0", 0.0), ConnectorRef("a1", 1.0)),
            length_m=100.0,
        ),
        RoadRecord(
            "b",
            LineString((Point(1, 0), Point(2, 0))),
            30,
            connectors=(ConnectorRef("b0", 0.0), ConnectorRef("b1", 1.0)),
            length_m=100.0,
        ),
    )

    result = build_topological_road_graph(roads)
    graph = result.graph
    start = graph.connector_nodes["a0"]
    end = graph.connector_nodes["b1"]

    travel_time, path = graph.shortest_path(start, end)
    assert travel_time == float("inf")
    assert path == ()
