from __future__ import annotations

import pandas as pd

from wikiroutes.dedup import dedup_analyze
from wikiroutes.enums import RouteType
from wikiroutes.models import Direction, RouteData


def _route(route_id: int, name: str, offset: float) -> RouteData:
    direction = Direction(
        coords=(
            (51.60 + offset, 39.18),
            (51.61 + offset, 39.19),
        ),
        stops=(),
    )
    return RouteData(
        name=name,
        route_type=RouteType.BUS,
        route_id=route_id,
        url="",
        directions=(direction,),
    )


def test_dedup_analyze_uses_stable_direction_key_in_per_direction_mode() -> None:
    routes = [_route(10, "10", 0.0), _route(20, "20", 0.0001)]
    analysis = dedup_analyze(routes, buffer_r=100.0, thr=0.7, per_direction=True)

    assert analysis is not None
    assert set(analysis["ids"]) == {(10, 0), (20, 0)}
    assert set(analysis["meta"]) == {(10, 0), (20, 0)}
    assert all(key in analysis["lengths"] for key in {(10, 0), (20, 0)})


def test_dedup_pairs_keep_direction_keys() -> None:
    routes = [_route(10, "10", 0.0), _route(20, "20", 0.0001)]
    analysis = dedup_analyze(routes, buffer_r=100.0, thr=0.7, per_direction=True)

    assert analysis is not None
    pairs = analysis["pairs"]
    assert isinstance(pairs, pd.DataFrame)
    if not pairs.empty:
        assert all(isinstance(value, tuple) for value in pairs["x"])
        assert all(isinstance(value, tuple) for value in pairs["y"])
