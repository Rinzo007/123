import json

import pytest

from transit_planner.demand_points import load_demand_points


def _write(tmp_path, entries, name="demand_data.json"):
    path = tmp_path / name
    path.write_text(json.dumps({"points": entries}), encoding="utf-8")
    return path


def test_reads_points_and_projects_them(tmp_path):
    path = _write(tmp_path, [
        {"id": "a", "location": [13.4, 52.5], "residents": 41, "jobs": 0},
        {"id": "b", "location": [13.5, 52.5], "residents": 7, "jobs": 3},
    ])
    zones, source = load_demand_points(path, origin_lon=13.4, origin_lat=52.5)

    assert len(zones) == 2
    assert [z.id for z in zones] == ["a", "b"]
    assert [z.population for z in zones] == [41.0, 7.0]
    assert [z.jobs for z in zones] == [0.0, 3.0]
    # Первая точка совпадает с началом координат; вторая смещена на 0.1°
    # долготы, что на 52.5° параллели около 6.78 км.
    assert zones[0].centroid_x == pytest.approx(0.0, abs=1e-6)
    assert zones[1].centroid_x == pytest.approx(6776.7, rel=1e-3)
    assert zones[1].centroid_y == pytest.approx(0.0, abs=1e-6)
    assert source.total_residents == 48.0
    assert source.count == 2


def test_area_filter_drops_outside_points(tmp_path):
    path = _write(tmp_path, [
        {"id": "in", "location": [13.40, 52.50], "residents": 10, "jobs": 5},
        {"id": "out", "location": [20.00, 52.50], "residents": 99, "jobs": 9},
    ])
    zones, source = load_demand_points(
        path, origin_lon=13.4, origin_lat=52.5, inside=lambda lon, _lat: lon < 14.0
    )
    assert [z.id for z in zones] == ["in"]
    assert source.count == 2, "исходное число точек остаётся видным"
    assert source.inside_count == 1
    assert source.total_residents == 10.0


def test_counts_empty_points_without_dropping_them(tmp_path):
    """Точка без населения полезна: в ней живут работающие."""
    path = _write(tmp_path, [
        {"id": "home", "location": [13.40, 52.50], "residents": 0, "jobs": 40},
        {"id": "work", "location": [13.41, 52.50], "residents": 0, "jobs": 0},
    ])
    zones, source = load_demand_points(path, origin_lon=13.4, origin_lat=52.5)
    assert len(zones) == 2
    assert source.zero_residents == 2
    assert source.zero_jobs == 1
    assert source.total_jobs == 40.0


def test_missing_id_and_location_are_tolerated(tmp_path):
    path = _write(tmp_path, [
        {"location": [13.40, 52.50], "residents": 3, "jobs": 1},
        {"id": "x", "residents": 5, "jobs": 1},
        {"id": "y", "location": [13.41, 52.50], "residents": 4, "jobs": 1},
    ])
    zones, _ = load_demand_points(path, origin_lon=13.4, origin_lat=52.5)
    # Точка без координат пропускается, без id - получает сгенерированный.
    assert len(zones) == 2
    assert zones[0].id.startswith("pt")


def test_bare_list_is_accepted(tmp_path):
    path = tmp_path / "points.json"
    path.write_text(
        json.dumps([{"id": "a", "location": [13.4, 52.5], "residents": 1, "jobs": 1}]),
        encoding="utf-8",
    )
    zones, _ = load_demand_points(path, origin_lon=13.4, origin_lat=52.5)
    assert len(zones) == 1


def test_empty_and_missing_points_are_rejected(tmp_path):
    empty = _write(tmp_path, [], name="empty.json")
    with pytest.raises(ValueError, match="no points"):
        load_demand_points(empty, origin_lon=0.0, origin_lat=0.0)

    nothing_outside = _write(tmp_path, [
        {"id": "a", "location": [20.0, 52.5], "residents": 1, "jobs": 1},
    ], name="outside.json")
    with pytest.raises(ValueError, match="no points inside"):
        load_demand_points(
            nothing_outside, origin_lon=13.4, origin_lat=52.5,
            inside=lambda lon, _lat: lon < 14.0,
        )


def test_provenance_is_serialisable(tmp_path):
    path = _write(tmp_path, [
        {"id": "a", "location": [13.4, 52.5], "residents": 10, "jobs": 4},
    ])
    _, source = load_demand_points(path, origin_lon=13.4, origin_lat=52.5)
    payload = source.to_provenance()
    json.dumps(payload)
    assert payload["pointsInsideStudyArea"] == 1
    assert payload["residentsInside"] == 10.0
