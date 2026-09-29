from transit_planner.data import ConnectorRef, RoadRecord
from transit_planner.geo import LineString, Point
from transit_planner.road_builder import build_topological_road_graph


def test_shared_connector_creates_shared_graph_node():
    roads = (
        RoadRecord(
            id="a",
            geometry=LineString((Point(0, 0), Point(10, 0))),
            speed_kph=30,
            connectors=(
                ConnectorRef("left", 0.0),
                ConnectorRef("junction", 1.0),
            ),
        ),
        RoadRecord(
            id="b",
            geometry=LineString((Point(10, 0), Point(10, 10))),
            speed_kph=30,
            connectors=(
                ConnectorRef("junction", 0.0),
                ConnectorRef("top", 1.0),
            ),
        ),
    )

    result = build_topological_road_graph(roads)
    graph = result.graph
    start = graph.connector_nodes["left"]
    end = graph.connector_nodes["top"]
    junction = graph.connector_nodes["junction"]

    time, path = graph.shortest_path(start, end)
    assert round(time, 6) == round(20 / 1000 / 30 * 60, 6)
    assert len(path) == 2
    assert graph.edges[path[0]].to_node == junction
    assert graph.edges[path[1]].from_node == junction


def test_coincident_geometry_without_shared_connector_is_not_connected():
    roads = (
        RoadRecord(
            id="a",
            geometry=LineString((Point(0, 0), Point(10, 0))),
            speed_kph=30,
            connectors=(ConnectorRef("a0", 0.0), ConnectorRef("a1", 1.0)),
        ),
        RoadRecord(
            id="b",
            geometry=LineString((Point(10, 0), Point(20, 0))),
            speed_kph=30,
            connectors=(ConnectorRef("b0", 0.0), ConnectorRef("b1", 1.0)),
        ),
    )
    result = build_topological_road_graph(roads)
    time, _ = result.graph.shortest_path(
        result.graph.connector_nodes["a0"],
        result.graph.connector_nodes["b1"],
    )
    assert time == float("inf")


def test_interior_connector_splits_geometry_by_linear_reference():
    roads = (
        RoadRecord(
            id="s",
            geometry=LineString((Point(0, 0), Point(10, 0), Point(20, 0))),
            speed_kph=60,
            connectors=(
                ConnectorRef("a", 0.0),
                ConnectorRef("mid", 0.25),
                ConnectorRef("b", 1.0),
            ),
        ),
    )
    result = build_topological_road_graph(roads)
    graph = result.graph
    time_a_mid, _ = graph.shortest_path(
        graph.connector_nodes["a"],
        graph.connector_nodes["mid"],
    )
    time_mid_b, _ = graph.shortest_path(
        graph.connector_nodes["mid"],
        graph.connector_nodes["b"],
    )
    assert round(time_a_mid, 6) == round(5 / 1000 / 60 * 60, 6)
    assert round(time_mid_b, 6) == round(15 / 1000 / 60 * 60, 6)


def test_zero_length_span_is_dropped_but_counted():
    """Два коннектора в одной точке дают ребро нулевой длины.

    Встречается в данных на Киеве (один сегмент из 83 662), и раньше такой
    сегмент останавливал сборку всего города. Ребро не несёт геометрии, но
    дефект обязан остаться виден, поэтому он считается, а не роняет граф.
    """
    roads = (
        RoadRecord(
            id="degenerate",
            geometry=LineString((Point(0, 0), Point(10, 0), Point(20, 0))),
            speed_kph=30,
            connectors=(
                ConnectorRef("same", 0.0),
                ConnectorRef("also_same", 0.0),
                ConnectorRef("end", 1.0),
            ),
        ),
    )

    result = build_topological_road_graph(roads)

    assert result.degenerate_segments == ("degenerate",)
    # Узлы на месте, а между совпавшими в одной точке коннекторами ребра нет.
    nodes = result.graph.connector_nodes
    assert set(nodes) == {"same", "also_same", "end"}
    connected = {
        (edge.from_node, edge.to_node) for edge in result.graph.edges.values()
    } | {
        (edge.to_node, edge.from_node) for edge in result.graph.edges.values()
    }
    assert (nodes["same"], nodes["also_same"]) not in connected
    assert (nodes["also_same"], nodes["end"]) in connected


def test_clean_segment_reports_no_degenerate():
    roads = (
        RoadRecord(
            id="clean",
            geometry=LineString((Point(0, 0), Point(10, 0))),
            speed_kph=30,
            connectors=(ConnectorRef("a", 0.0), ConnectorRef("b", 1.0)),
        ),
    )
    assert build_topological_road_graph(roads).degenerate_segments == ()


def test_connector_order_in_source_does_not_matter():
    """Ссылки сортируются по at, поэтому порядок в данных не влияет.

    UUID коннекторов непрозрачны, а в Overture они идут в произвольном
    порядке: 70% сегментов Киева не отсортированы по строке. Сортировка по
    at - единственное, что делает порядок значимым.
    """
    roads = (
        RoadRecord(
            id="unsorted",
            geometry=LineString((Point(0, 0), Point(10, 0))),
            speed_kph=30,
            connectors=(ConnectorRef("b", 1.0), ConnectorRef("a", 0.0)),
        ),
    )
    result = build_topological_road_graph(roads)
    assert result.degenerate_segments == ()
    # Одно ребро туда и одно обратно, порядок в данных на это не влияет.
    assert len(result.graph.edges) == 2
