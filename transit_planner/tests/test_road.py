from transit_planner.data import GeoJSONRoadProvider
from transit_planner.road_builder import build_road_graph
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
