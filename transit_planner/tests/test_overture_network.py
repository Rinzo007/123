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
        __import__("transit_planner.data", fromlist=["ConnectorRecord"]).ConnectorRecord(
            "c0", Point(39.2000, 51.6700)
        ),
        __import__("transit_planner.data", fromlist=["ConnectorRecord"]).ConnectorRecord(
            "c1", Point(39.2010, 51.6700)
        ),
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
