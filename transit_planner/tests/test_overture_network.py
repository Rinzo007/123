from transit_planner.binary_pack import decode_streets_bin
from transit_planner.city_pack import streets_bin_for_graph
from transit_planner.data import ConnectorRecord, ConnectorRef, ProhibitedTransition, ProhibitedTransitionSequenceEntry, RoadRecord
from transit_planner.geo import LineString, Point
from transit_planner.network import Stop
from transit_planner.overture_network import build_overture_network


def test_build_overture_network_projects_and_snaps_stops():
    roads = (
        RoadRecord(
            "overture:r1",
            LineString((Point(39.2000, 51.6700), Point(39.2010, 51.6700))),
            30.0,
            connectors=(ConnectorRef("c0", 0.0), ConnectorRef("c1", 1.0)),
            length_m=70.0,
        ),
    )
    connectors = (
        ConnectorRecord("c0", Point(39.2000, 51.6700)),
        ConnectorRecord("c1", Point(39.2010, 51.6700)),
    )
    stops = (
        Stop("s1", "Test", Point(39.20005, 51.6700)),
    )

    result = build_overture_network(
        roads,
        connectors,
        stops,
        origin_lon=39.2000,
        origin_lat=51.6700,
        snap_max_distance_m=100.0,
    )

    assert result.graph_build.connector_count == 2
    assert result.stop_snaps[0].road_node_id == result.graph.connector_nodes["c0"]
    assert result.stop_snaps[0].distance < 100.0
    assert result.roads_metric[0].length_m == 70.0


def test_build_overture_network_preserves_turn_restrictions():
    roads = (
        RoadRecord(
            "overture:a",
            LineString((Point(39.2000, 51.6700), Point(39.2010, 51.6700))),
            30.0,
            connectors=(ConnectorRef("c0", 0.0), ConnectorRef("c1", 1.0)),
            prohibited_transitions=(
                ProhibitedTransition(
                    "overture:a",
                    (ProhibitedTransitionSequenceEntry("overture:b", "c1"),),
                    final_heading="forward",
                    when_heading="forward",
                ),
            ),
            length_m=70.0,
        ),
        RoadRecord(
            "overture:b",
            LineString((Point(39.2010, 51.6700), Point(39.2020, 51.6700))),
            30.0,
            connectors=(ConnectorRef("c1", 0.0), ConnectorRef("c2", 1.0)),
            length_m=70.0,
        ),
    )

    result = build_overture_network(
        roads,
        (),
        (),
        origin_lon=39.2010,
        origin_lat=51.6700,
        snap_max_distance_m=None,
    )
    time, path = result.graph.shortest_path(
        result.graph.connector_nodes["c0"],
        result.graph.connector_nodes["c2"],
    )

    assert time == float("inf")
    assert path == ()


def test_overture_route_points_returns_graph_geometry_and_metrics():
    roads = (
        RoadRecord(
            "overture:r1",
            LineString((
                Point(39.2000, 51.6700),
                Point(39.2010, 51.6700),
                Point(39.2020, 51.6700),
            )),
            30.0,
            oneway=True,
            connectors=(ConnectorRef("c0", 0.0), ConnectorRef("c1", 0.5), ConnectorRef("c2", 1.0)),
            length_m=2000.0,
        ),
    )
    result = build_overture_network(
        roads,
        (),
        (),
        origin_lon=39.2010,
        origin_lat=51.6700,
        snap_max_distance_m=500.0,
    )

    route = result.route_points((
        Point(39.20002, 51.6700),
        Point(39.20198, 51.6700),
    ))

    assert route.edge_ids == ("overture:r1:c0:c1", "overture:r1:c1:c2")
    assert abs(route.length_m - 2000.0) < 1e-6
    assert abs(route.travel_time_min - 4.0) < 1e-6
    assert len(route.geometry) >= 3


def test_route_points_across_components_raises_explicitly():
    roads = (
        RoadRecord(
            "left",
            LineString((Point(39.2000, 51.6700), Point(39.2010, 51.6700))),
            30.0,
            connectors=(ConnectorRef("l0", 0.0), ConnectorRef("l1", 1.0)),
            length_m=70.0,
        ),
        RoadRecord(
            "right",
            LineString((Point(39.2100, 51.6700), Point(39.2110, 51.6700))),
            30.0,
            connectors=(ConnectorRef("r0", 0.0), ConnectorRef("r1", 1.0)),
            length_m=70.0,
        ),
    )
    result = build_overture_network(
        roads,
        (),
        (),
        origin_lon=39.2010,
        origin_lat=51.6700,
        snap_max_distance_m=500.0,
    )

    try:
        result.route_points((
            Point(39.20005, 51.6700),
            Point(39.21095, 51.6700),
        ))
    except ValueError as error:
        assert "disconnected" in str(error)
    else:
        raise AssertionError("cross-component routing must raise")


def test_streets_bin_roundtrip_from_real_graph():
    roads = (
        RoadRecord(
            "r1",
            LineString((Point(39.2000, 51.6700), Point(39.2010, 51.6700))),
            40.0,
            road_type="secondary",
            oneway=True,
            connectors=(ConnectorRef("c0", 0.0), ConnectorRef("c1", 0.5), ConnectorRef("c2", 1.0)),
            length_m=140.0,
        ),
    )
    result = build_overture_network(
        roads,
        (),
        (),
        origin_lon=39.2010,
        origin_lat=51.6700,
        snap_max_distance_m=500.0,
    )

    payload, stats = streets_bin_for_graph(
        result.graph,
        origin_lon=result.origin_lon,
        origin_lat=result.origin_lat,
    )
    assert stats["nodes"] == 3
    assert stats["edges"] == 2
    assert stats["components"] == 1

    graph = decode_streets_bin(payload)
    assert graph["vertex_count"] == 3
    assert graph["edge_count"] == 2
    assert len(set(graph["component"])) == 1
    assert graph["edge_speed_kph"] == [40, 40]
    assert graph["edge_direction"] == [1, 1]
    assert graph["edge_class"] == [1, 1]
    assert graph["edge_len_m"] == [70, 70]
    for index in range(3):
        assert abs(graph["lon"][index] / 1e6 - (39.2 + index * 0.0005)) < 1e-6
        assert abs(graph["lat"][index] / 1e6 - 51.67) < 1e-6
