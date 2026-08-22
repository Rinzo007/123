from __future__ import annotations

from types import SimpleNamespace

from wikiroutes.dedup_geometry import dedupe_xy, safe_float
from wikiroutes.pipeline_helpers import apply_active_filter, split_route_errors


def test_safe_float_rejects_non_finite() -> None:
    assert safe_float("1.5") == 1.5
    assert safe_float(float("nan"), 7.0) == 7.0
    assert safe_float("bad", 3.0) == 3.0


def test_dedupe_xy_removes_adjacent_grid_duplicates() -> None:
    points = [(0.0, 0.0), (0.004, 0.004), (0.02, 0.0), (0.021, 0.0)]
    assert dedupe_xy(points, eps=0.01) == [
        (0.0, 0.0),
        (0.02, 0.0),
    ]


def test_apply_active_filter_preserves_all_when_disabled() -> None:
    routes = [SimpleNamespace(active=True), SimpleNamespace(active=False)]
    result, skipped = apply_active_filter(routes, active_only=False)
    assert len(result) == 2
    assert skipped == 0


def test_split_route_errors() -> None:
    ok = SimpleNamespace(error=None, directions=(object(),))
    bad = SimpleNamespace(error="boom", directions=())
    valid, invalid = split_route_errors([ok, bad])
    assert valid == [ok]
    assert invalid == [bad]
