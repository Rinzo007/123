from transit_planner.data import ConnectorRef
from transit_planner.overture import (
    DEFAULT_RELEASE,
    OvertureConnectorProvider,
    OvertureSource,
    OvertureTransitProvider,
    OvertureTransportationProvider,
    _haversine_linestring_m,
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
