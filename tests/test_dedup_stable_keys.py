from __future__ import annotations

import pandas as pd

from dedup import dedup_network_after, materialize_dedup_matrix
from enums import RouteType
from models import Direction, RouteData
from units import build_units, network_center_distances


def _route(route_id: int, coords: tuple[tuple[float, float], ...]) -> RouteData:
    return RouteData(
        name=str(route_id),
        route_type=RouteType.BUS,
        route_id=route_id,
        url="",
        directions=(Direction(coords=coords, stops=()),),
    )


def test_center_distances_are_keyed_by_direction_key() -> None:
    units = build_units([
        _route(10, ((51.67, 39.20), (51.68, 39.21))),
        _route(20, ((51.69, 39.22), (51.70, 39.23))),
    ])

    distances = network_center_distances(units)

    assert set(distances) == {(10, 0), (20, 0)}


def test_materialize_matrix_accepts_direction_keys() -> None:
    pairs = pd.DataFrame(
        [((10, 0), (20, 0), 0.85)],
        columns=["x", "y", "K2_max"],
    )

    matrix = materialize_dedup_matrix({
        "ids": [(10, 0), (20, 0)],
        "pairs": pairs,
    })

    assert matrix.loc["(10, 0)", "(20, 0)"] == 0.85
    assert matrix.loc["(20, 0)", "(10, 0)"] == 0.85


def test_network_after_accepts_direction_keys() -> None:
    from pyproj import Transformer
    from shapely.geometry import LineString

    direction = _route(
        10,
        ((51.67, 39.20), (51.68, 39.21)),
    ).directions[0]

    transformer = Transformer.from_crs("EPSG:4326", "EPSG:32637", always_xy=True)
    xs, ys = transformer.transform(*zip(*direction.coords))
    line = LineString(list(zip(xs, ys)))

    total_km, unique_km, coefficient = dedup_network_after(
        {"lines": {(10, 0): [line]}},
        {(10, 0)},
    )

    assert total_km > 0.0
    assert unique_km > 0.0
    assert coefficient == 1.0
