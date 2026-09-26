from transit_planner.data import ConnectorRef, ProhibitedTransitionSequenceEntry, ProhibitedTransition, RoadRecord
from transit_planner.geo import LineString, Point
from transit_planner.overture import (
    DEFAULT_RELEASE,
    OvertureConnectorProvider,
    OvertureSource,
    OvertureTransitProvider,
    OvertureTransportationProvider,
    _access_directions,
    _effective_speed_kph,
    _haversine_linestring_m,
    _parse_prohibited_transitions,
    _is_oneway,
    _parse_connector_refs,
)


def test_overture_defaults_to_current_patch_release():
    assert DEFAULT_RELEASE == "2026-09-23.1"
    source = OvertureSource()
    assert "theme=transportation/type=segment" in source.transportation_segments()
    assert "theme=transportation/type=connector" in source.transportation_connectors()
    assert "theme=base/type=infrastructure" in source.infrastructure()


def test_connector_reference_parser_accepts_duckdb_shapes():
    refs = _parse_connector_refs(
        [
            {"connector_id": "c2", "at": 1.0},
            {"connector_id": "c1", "at": 0.0},
            ("c3", 0.5),
        ]
    )
    assert refs == (
        ConnectorRef("c1", 0.0),
        ConnectorRef("c3", 0.5),
        ConnectorRef("c2", 1.0),
    )


def test_overture_sql_uses_connectors():
    provider = OvertureTransportationProvider(
        bbox=(51.6, 39.1, 51.7, 39.3),
    )
    sql = provider._sql()
    assert "connectors" in sql
    assert "prohibited_transitions" in sql
    assert "ST_AsGeoJSON(ST_GeomFromWKB(geometry))" in sql
    assert "bbox.xmin" in sql


def test_overture_connector_sql():
    provider = OvertureConnectorProvider(
        bbox=(51.6, 39.1, 51.7, 39.3),
    )
    sql = provider._sql()
    assert "theme=transportation/type=connector" in sql
    assert "ST_AsGeoJSON(ST_GeomFromWKB(geometry))" in sql


def test_overture_transit_sql_filters_transit_classes():
    provider = OvertureTransitProvider()
    sql = provider._sql()
    assert "subtype = 'transit'" in sql
    assert "bus_stop" in sql
    assert "subway_station" in sql


def test_overture_length_helper_returns_metric_polyline_length():
    from transit_planner.geo import Point

    length = _haversine_linestring_m((Point(39.2, 51.7), Point(39.21, 51.7)))
    assert 650.0 < length < 750.0

def test_explicit_backward_access_denial_marks_segment_oneway():
    assert _is_oneway([
        {"access_type": "denied", "when": {"heading": "backward"}}
    ])
    assert not _is_oneway([
        {"access_type": "denied", "when": {"heading": "backward", "mode": ["bus"]}}
    ])

def test_global_overture_speed_limit_overrides_class_speed():
    assert round(_effective_speed_kph([
        {"max_speed": {"value": 30, "unit": "mph"}}
    ], 60.0), 3) == round(30 * 1.609344, 3)
    assert _effective_speed_kph(
        [{"max_speed": {"value": 50, "unit": "km/h"}, "between": [0.0, 0.5]}],
        60.0,
    ) == 60.0

def test_prohibited_transition_parser_prefixes_segment_ids_and_skips_scoped_rules():
    raw = [
        {
            "sequence": [
                {"segment_id": "target", "connector_id": "c1"},
            ],
            "final_heading": "forward",
            "when": {"heading": "forward"},
        },
        {
            "sequence": [
                {"segment_id": "target-vehicle", "connector_id": "c2"},
            ],
            "final_heading": "forward",
            "when": {"mode": ["motor_vehicle"]},
        },
    ]

    parsed = _parse_prohibited_transitions(raw, source_segment_id="overture:source")

    assert len(parsed) == 1
    assert parsed[0].source_segment_id == "overture:source"
    assert parsed[0].sequence == (
        ProhibitedTransitionSequenceEntry("overture:target", "c1"),
    )


def test_overture_provider_load_graph_uses_connector_topology():
    provider = OvertureTransportationProvider()
    provider.load_roads = lambda: (
        RoadRecord(
            "overture:source",
            LineString((Point(0, 0), Point(1, 0))),
            30.0,
            connectors=(ConnectorRef("c0", 0.0), ConnectorRef("c1", 1.0)),
            prohibited_transitions=(
                ProhibitedTransition(
                    "overture:source",
                    (ProhibitedTransitionSequenceEntry("overture:target", "c1"),),
                ),
            ),
            length_m=100.0,
        ),
        RoadRecord(
            "overture:target",
            LineString((Point(1, 0), Point(2, 0))),
            30.0,
            connectors=(ConnectorRef("c1", 0.0), ConnectorRef("c2", 1.0)),
            length_m=100.0,
        ),
    )

    result = provider.load_graph()

    assert result.connector_count == 3
    assert len(result.graph.prohibited_transitions) == 1

def test_access_direction_parser_handles_forward_backward_and_global_denials():
    assert _access_directions([
        {"access_type": "denied", "when": {"heading": "backward"}}
    ]) == (True, False)
    assert _access_directions([
        {"access_type": "denied", "when": {"heading": "forward"}}
    ]) == (False, True)
    assert _access_directions([
        {"access_type": "denied"}
    ]) == (False, False)
    assert _access_directions([
        {"access_type": "denied"},
        {"access_type": "allowed", "when": {"heading": "forward"}},
    ]) == (True, False)
