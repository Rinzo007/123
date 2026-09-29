import pytest

from transit_planner.data import (
    ConnectorRecord,
    ConnectorRef,
    ProhibitedTransition,
    ProhibitedTransitionSequenceEntry,
    RoadRecord,
)
from transit_planner.geo import LineString, Point
from transit_planner.overture import (
    DEFAULT_RELEASE,
    OvertureConnectorProvider,
    OvertureSource,
    OvertureTransitProvider,
    OverturePlacesProvider,
    OvertureTransportationProvider,
    _access_directions,
    _effective_speed_kph,
    _haversine_linestring_m,
    _parse_prohibited_transitions,
    _is_oneway,
    _parquet_source,
    _parse_connector_refs,
    _stac_index_path,
    _stac_part_files,
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
    # geometry приходит нативным GEOMETRY: ST_GeomFromWKB такой тип не принимает.
    assert "ST_AsGeoJSON(geometry)" in sql
    assert "ST_GeomFromWKB(geometry)" not in sql
    assert "bbox.xmin" in sql


def test_overture_connector_sql():
    provider = OvertureConnectorProvider(
        bbox=(51.6, 39.1, 51.7, 39.3),
    )
    sql = provider._sql()
    assert "theme=transportation/type=connector" in sql
    # geometry приходит нативным GEOMETRY: ST_GeomFromWKB такой тип не принимает.
    assert "ST_AsGeoJSON(geometry)" in sql
    assert "ST_GeomFromWKB(geometry)" not in sql


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


def test_overture_provider_load_graph_uses_connector_topology(monkeypatch):
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
    requested: list[int] = []

    def fake_load_connectors(_self):
        # Коннекторы читаются по расширенной области: без их позиций граф не
        # собирается, так как интерполяция по двум сегментам расходится.
        requested.append(1)
        return (
            ConnectorRecord("c0", Point(0.0, 0.0)),
            ConnectorRecord("c1", Point(1.0, 0.0)),
            ConnectorRecord("c2", Point(2.0, 0.0)),
        )

    monkeypatch.setattr(
        OvertureConnectorProvider, "load_connectors", fake_load_connectors, raising=True
    )

    result = provider.load_graph()

    assert result.connector_count == 3
    assert len(result.graph.prohibited_transitions) == 1
    assert requested == [1], "коннекторы должны читаться ровно один раз"


def test_overture_connector_sql_uses_dilated_bbox():
    """Коннекторы нужны и за границей bbox: расширение обязано быть в SQL."""
    provider = OvertureConnectorProvider(bbox=(51.6, 39.1, 51.7, 39.3))
    sql = provider._sql()
    # Исходный bbox плюс запас: коннектор на стыке сегментов лежит снаружи.
    assert "bbox.xmin <= 39.31" in sql, sql
    assert "bbox.xmax >= 39.09" in sql
    assert "bbox.ymin <= 51.71" in sql
    assert "bbox.ymax >= 51.59" in sql


def test_overture_graph_prefers_connector_position_over_interpolation(monkeypatch):
    """Позиция коннектора авторитетна: расхождение с интерполяцией не должно
    валить сборку. Проверено на живых данных: у 62% коннекторов расхождение
    больше 1 см, а максимум достигает 77 м."""
    from transit_planner.road_builder import build_topological_road_graph

    roads = (
        RoadRecord(
            "a",
            LineString((Point(0.0, 0.0), Point(100.0, 0.0))),
            30.0,
            connectors=(ConnectorRef("shared", 0.5), ConnectorRef("tail", 1.0)),
            length_m=100.0,
        ),
        RoadRecord(
            "b",
            # Геометрия другого сегмента даёт для shared заметно другую точку.
            LineString((Point(100.0, 0.0), Point(100.0, 100.0))),
            30.0,
            connectors=(ConnectorRef("shared", 0.0), ConnectorRef("end", 1.0)),
            length_m=100.0,
        ),
    )
    # Авторитетная позиция совпадает с интерполяцией первого сегмента.
    locations = {"shared": Point(50.0, 0.0), "tail": Point(100.0, 0.0),
                 "end": Point(100.0, 100.0)}
    result = build_topological_road_graph(roads, connector_locations=locations)
    assert result.connector_count == 3
    shared_node = result.graph.nodes[result.graph.connector_nodes["shared"]]
    assert (shared_node.x, shared_node.y) == (50.0, 0.0)


def test_overture_graph_still_rejects_unknown_connector_conflict():
    """Без известной позиции коннектора конфликт остаётся ошибкой, а не молчаливым
    выбором одной из двух точек: данных для решения нет."""
    from transit_planner.road_builder import build_topological_road_graph

    roads = (
        RoadRecord(
            "a",
            LineString((Point(0.0, 0.0), Point(100.0, 0.0))),
            30.0,
            connectors=(ConnectorRef("shared", 0.5), ConnectorRef("tail", 1.0)),
            length_m=100.0,
        ),
        RoadRecord(
            "b",
            LineString((Point(100.0, 0.0), Point(100.0, 100.0))),
            30.0,
            connectors=(ConnectorRef("shared", 0.0), ConnectorRef("end", 1.0)),
            length_m=100.0,
        ),
    )
    locations = {"tail": Point(100.0, 0.0), "end": Point(100.0, 100.0)}
    with pytest.raises(ValueError, match="conflicting coordinates"):
        build_topological_road_graph(roads, connector_locations=locations)

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


def test_overture_places_uses_current_taxonomy_fields():
    provider = OverturePlacesProvider(
        categories=("hospital", "school"),
    )
    sql = provider._sql()
    assert "theme=places/type=place/*" in sql
    assert "taxonomy.primary" in sql
    assert "basic_category" in sql
    assert "categories.primary" not in sql


def test_place_purpose_mapper_matches_basic_and_taxonomy_categories():
    from transit_planner.places import CityPlace, PlacePurpose, PlacePurposeMapper

    mapper = PlacePurposeMapper()
    assert mapper.purpose_for(
        CityPlace("1", "School", Point(0, 0), basic_category="school")
    ) == PlacePurpose.EDUCATION
    assert mapper.purpose_for(
        CityPlace("2", "Hospital", Point(0, 0), taxonomy_primary="hospital")
    ) == PlacePurpose.HEALTH
    assert mapper.purpose_for(
        CityPlace("3", "Unknown", Point(0, 0), basic_category="unknown")
    ) is None


def test_stac_part_files_absent_without_bbox(monkeypatch):
    """Без bbox части не выбираются: фильтровать нечем."""
    def boom(*_args, **_kwargs):
        raise AssertionError("STAC не должен вызываться без bbox")

    monkeypatch.setattr("transit_planner.overture._query_duckdb", boom)
    assert _stac_part_files(DEFAULT_RELEASE, "segment", None) is None


def test_stac_part_files_reads_assets_href(monkeypatch, tmp_path):
    index = tmp_path / "collections.parquet"
    index.write_bytes(b"stac")
    monkeypatch.setattr(
        "transit_planner.overture._stac_index_path", lambda _release: index
    )

    def fake_query(sql):
        assert "collection = 'segment'" in sql
        return (
            ({"aws": {"href": "https://s3/part-a.parquet"}},),
            ({"s3": {"href": "https://s3/part-b.parquet"}},),
            (None,),
            ({"aws": {"href": ""}},),
        )

    monkeypatch.setattr("transit_planner.overture._query_duckdb", fake_query)
    parts = _stac_part_files(DEFAULT_RELEASE, "segment", (52.4, 13.3, 52.5, 13.4))
    assert parts == ["https://s3/part-a.parquet", "https://s3/part-b.parquet"]


def test_stac_part_files_returns_none_on_failure(monkeypatch, tmp_path):
    index = tmp_path / "collections.parquet"
    index.write_bytes(b"stac")
    monkeypatch.setattr(
        "transit_planner.overture._stac_index_path", lambda _release: index
    )

    def boom(_sql):
        raise RuntimeError("STAC недоступен")

    monkeypatch.setattr("transit_planner.overture._query_duckdb", boom)
    assert _stac_part_files(DEFAULT_RELEASE, "segment", (52.4, 13.3, 52.5, 13.4)) is None


def test_parquet_source_quotes_parts_and_falls_back(monkeypatch):
    monkeypatch.setattr(
        "transit_planner.overture._stac_part_files",
        lambda *_args: ["https://s3/a.parquet", "https://s3/b.parquet"],
    )
    listed = _parquet_source(
        "s3://bucket/theme=x/*",
        release=DEFAULT_RELEASE,
        overture_type="segment",
        bbox=(52.4, 13.3, 52.5, 13.4),
    )
    # В списочной форме элементы обязаны быть строковыми литералами.
    assert listed == "['https://s3/a.parquet', 'https://s3/b.parquet']"

    monkeypatch.setattr(
        "transit_planner.overture._stac_part_files", lambda *_args: None
    )
    assert (
        _parquet_source(
            "s3://bucket/theme=x/*",
            release=DEFAULT_RELEASE,
            overture_type="segment",
            bbox=(52.4, 13.3, 52.5, 13.4),
        )
        == "s3://bucket/theme=x/*"
    )


def test_stac_index_download_failure_returns_none(monkeypatch, tmp_path):
    """Падение закачки индекса не должно ломать чтение: вернётся glob."""
    monkeypatch.setattr(
        "transit_planner.overture.tempfile.gettempdir", lambda: str(tmp_path)
    )

    def boom(*_args, **_kwargs):
        raise OSError("сети нет")

    monkeypatch.setattr("transit_planner.overture.urlopen", boom)
    monkeypatch.setattr("transit_planner.overture.time.sleep", lambda _s: None)
    assert _stac_index_path(DEFAULT_RELEASE) is None
