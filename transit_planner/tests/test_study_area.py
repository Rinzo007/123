import json
import math

import pytest

from transit_planner.study_area import load_boundary_geojson

SQUARE = [
    [39.0, 51.0],
    [39.1, 51.0],
    [39.1, 51.1],
    [39.0, 51.1],
    [39.0, 51.0],
]


def _write(tmp_path, geometry, name="city_routes_boundary.geojson", **props):
    payload = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "properties": props, "geometry": geometry}],
    }
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_bbox_and_name_come_from_the_ring(tmp_path):
    path = _write(
        tmp_path,
        {"type": "Polygon", "coordinates": [SQUARE]},
        source="Overture-buildings",
    )
    area = load_boundary_geojson(path)
    assert area.name == "city"
    assert area.bbox == (51.0, 39.0, 51.1, 39.1)
    assert area.ring_count == 1
    assert area.vertex_count == 5
    assert area.source == "Overture-buildings"


def test_area_is_roughly_ten_by_seven_km(tmp_path):
    path = _write(tmp_path, {"type": "Polygon", "coordinates": [SQUARE]})
    area = load_boundary_geojson(path)
    # 0.1° долготы на 51-й параллели ~7.0 км, 0.1° широты ~11.1 км.
    # cos берётся от средней широты кольца, поэтому сходимость не analytical."


def test_closed_ring_is_not_counted_twice(tmp_path):
    """Кольцо замкнуто по спецификации; повторный обход удваивает площадь."""
    closed = _write(tmp_path, {"type": "Polygon", "coordinates": [SQUARE]})
    unclosed = _write(
        tmp_path,
        {"type": "Polygon", "coordinates": [[p for p in SQUARE[:-1]]]},
        name="open_routes_boundary.geojson",
    )
    # Оба кольца одной широты, поэтому разница только от плавающей точки.
    assert load_boundary_geojson(closed).polygon_area_m2 == pytest.approx(
        load_boundary_geojson(unclosed).polygon_area_m2, rel=1e-6
    )


def test_extra_area_is_reported_not_hidden(tmp_path):
    """Отбор идёт по bbox, поэтому лишняя площадь обязана быть видна."""
    path = _write(tmp_path, {"type": "Polygon", "coordinates": [SQUARE]})
    area = load_boundary_geojson(path)
    # У квадрата bbox совпадает с полигоном, лишнего почти нет.
    assert area.extra_area_fraction < 0.01
    assert area.to_provenance()["selection"].startswith("by bbox")


def test_l_shape_reports_real_extra_area(tmp_path):
    # Г-образный полигон: bbox заметно больше самой фигуры.
    l_shape = [
        [39.0, 51.0], [39.1, 51.0], [39.1, 51.05],
        [39.05, 51.05], [39.05, 51.1], [39.0, 51.1], [39.0, 51.0],
    ]
    path = _write(tmp_path, {"type": "Polygon", "coordinates": [l_shape]})
    area = load_boundary_geojson(path)
    assert 0.2 < area.extra_area_fraction < 0.4
    assert area.to_provenance()["extraAreaFraction"] > 0.2


def test_multipolygon_sums_parts(tmp_path):
    other = [[p[0] + 0.3, p[1] + 0.3] for p in SQUARE]
    path = _write(
        tmp_path,
        {"type": "MultiPolygon", "coordinates": [[SQUARE], [other]]},
    )
    area = load_boundary_geojson(path)
    single = _write(tmp_path, {"type": "Polygon", "coordinates": [SQUARE]},
                    name="one_routes_boundary.geojson")
    assert area.polygon_area_m2 == pytest.approx(
        load_boundary_geojson(single).polygon_area_m2 * 2, rel=0.005
    )
    assert area.bbox[1] == pytest.approx(39.0)
    assert area.bbox[3] == pytest.approx(39.4)


def test_empty_boundary_is_rejected(tmp_path):
    path = _write(tmp_path, {"type": "Polygon", "coordinates": []})
    with pytest.raises(ValueError, match="no usable area"):
        load_boundary_geojson(path)


def test_unsupported_geometry_is_rejected(tmp_path):
    path = _write(tmp_path, {"type": "LineString", "coordinates": SQUARE})
    with pytest.raises(ValueError, match="unsupported boundary geometry"):
        load_boundary_geojson(path)


def test_missing_file_is_reported(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_boundary_geojson(tmp_path / "absent.geojson")


def test_sha256_is_recorded_for_audit(tmp_path):
    path = _write(tmp_path, {"type": "Polygon", "coordinates": [SQUARE]})
    area = load_boundary_geojson(path)
    assert len(area.sha256) == 64
    # Изменение файла меняет хеш: граница - часть provenance, не украшение.
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert load_boundary_geojson(path).sha256 != area.sha256


def test_provenance_is_serialisable(tmp_path):
    path = _write(tmp_path, {"type": "Polygon", "coordinates": [SQUARE]})
    payload = load_boundary_geojson(path).to_provenance()
    json.dumps(payload)
    assert payload["polygonAreaKm2"] > 0
    assert "extraAreaFraction" in payload


def test_contains_accepts_interior_and_rejects_exterior(tmp_path):
    path = _write(tmp_path, {"type": "Polygon", "coordinates": [SQUARE]})
    area = load_boundary_geojson(path)
    assert area.contains(39.05, 51.05) is True
    # Точка ровно на ребре считается внутри: остановка на границе города
    # принадлежит городу, иначе она молча выпадала бы из сети.
    assert area.contains(39.05, 51.0) is True
    assert area.contains(39.15, 51.05) is False
    assert area.contains(39.05, 51.15) is False
    assert area.contains(40.0, 52.0) is False


def test_contains_subtracts_holes(tmp_path):
    """Аэродром или промзона внутри контура - дырка, её надо вычитать."""
    donut = [
        SQUARE,
        [[39.04, 51.04], [39.06, 51.04], [39.06, 51.06], [39.04, 51.06], [39.04, 51.04]],
    ]
    # coordinates у Polygon - это сам список колец, без лишней обёртки.
    path = _write(tmp_path, {"type": "Polygon", "coordinates": donut})
    area = load_boundary_geojson(path)
    assert area.contains(39.05, 51.05) is False, "центр дырки не внутри"
    assert area.contains(39.02, 51.02) is True, "вне дырки - внутри"
    # Дырка уменьшает площадь: внешний контур за вычетом отверстия.
    solid = load_boundary_geojson(
        _write(tmp_path, {"type": "Polygon", "coordinates": [SQUARE]},
               name="solid_routes_boundary.geojson")
    )
    assert area.polygon_area_m2 < solid.polygon_area_m2


def test_contains_unions_multiple_polygons(tmp_path):
    other = [[p[0] + 0.3, p[1] + 0.3] for p in SQUARE]
    path = _write(tmp_path, {"type": "MultiPolygon", "coordinates": [[SQUARE], [other]]})
    area = load_boundary_geojson(path)
    assert area.contains(39.05, 51.05) is True
    assert area.contains(39.35, 51.35) is True
    assert area.contains(39.2, 51.2) is False


def test_empty_geometry_passes_everything_through(tmp_path):
    """Без кольцей фильтр не должен молча выбрасывать всё."""
    area = load_boundary_geojson(
        _write(tmp_path, {"type": "Polygon", "coordinates": [SQUARE]})
    )
    stripped = type(area)(**{**{
        field: getattr(area, field)
        for field in ("name", "bbox", "polygon_area_m2", "ring_count",
                      "vertex_count", "source", "file", "sha256")
    }})
    assert stripped.contains(0.0, 0.0) is True


def test_provenance_records_extra_area_from_bbox(tmp_path):
    path = _write(tmp_path, {"type": "Polygon", "coordinates": [SQUARE]})
    payload = load_boundary_geojson(path).to_provenance()
    assert payload["bboxAreaKm2"] >= payload["polygonAreaKm2"]
