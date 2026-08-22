from __future__ import annotations

from types import SimpleNamespace

from wikiroutes.pipeline_bbox import route_inside_bbox, select_secondary_routes
from wikiroutes.pipeline_dedup import affected_route_ids, apply_dedup_removals, dir_stat_volume_map
from wikiroutes.pipeline_tasks import build_base_tasks, build_secondary_tasks


def test_route_inside_bbox() -> None:
    direction = SimpleNamespace(coords=((51.1, 39.1), (51.2, 39.2)))
    route = SimpleNamespace(error=None, directions=(direction,))
    assert route_inside_bbox(route, (51.0, 39.0, 51.3, 39.3))
    assert not route_inside_bbox(route, (51.15, 39.0, 51.3, 39.3))


def test_select_secondary_routes_without_filter() -> None:
    route = SimpleNamespace(error=None, directions=(SimpleNamespace(coords=((1.0, 1.0), (1.1, 1.1))),))
    assert select_secondary_routes([route], None, True) == [route]


def test_apply_dedup_removals_keeps_other_directions() -> None:
    d0 = SimpleNamespace(coords=((1.0, 1.0), (1.1, 1.1)))
    d1 = SimpleNamespace(coords=((2.0, 2.0), (2.1, 2.1)))
    route = SimpleNamespace(route_id=10, directions=(d0, d1))
    result, fully_removed, shortened = apply_dedup_removals(
        [route], {7: {"route_id": 10, "di": 0}}, {7}
    )
    assert result[0].directions == (d1,)
    assert fully_removed == 0
    assert shortened == 1


def test_affected_route_ids() -> None:
    assert affected_route_ids({1, 2}, {1: {"route_id": 10}, 2: {"route_id": 20}}) == {10, 20}


def test_dir_stat_volume_map() -> None:
    stat = SimpleNamespace(volume_m3=123.5)
    result = dir_stat_volume_map([3], {3: {"route_id": 10, "di": 1}}, {(10, 1): stat}, "volume_m3")
    assert result == {3: 123.5}
