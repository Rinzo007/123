from transit_planner.overture import (
    DEFAULT_RELEASE,
    OvertureSource,
    OvertureTransitProvider,
    OvertureTransportationProvider,
)


def test_overture_defaults_to_current_patch_release():
    assert DEFAULT_RELEASE == "2026-09-23.1"
    source = OvertureSource()
    assert "theme=transportation/type=segment" in source.transportation_segments()
    assert "theme=base/type=infrastructure" in source.infrastructure()


def test_overture_sql_uses_native_geometry():
    provider = OvertureTransportationProvider(
        bbox=(51.6, 39.1, 51.7, 39.3),
    )
    sql = provider._sql()
    assert "ST_AsGeoJSON(geometry)" in sql
    assert "ST_GeomFromWKB" not in sql
    assert "bbox.xmin" in sql


def test_overture_transit_sql_filters_transit_classes():
    provider = OvertureTransitProvider()
    sql = provider._sql()
    assert "subtype = 'transit'" in sql
    assert "bus_stop" in sql
    assert "subway_station" in sql
