from types import SimpleNamespace

from wikiroutes.xlsx_helpers import network_density, route_fully_in_bbox
from wikiroutes.xlsx_summary import summary_type_rows


def _route(route_type: str, coords: list[tuple[float, float]]) -> SimpleNamespace:
    return SimpleNamespace(
        route_type=route_type,
        directions=[SimpleNamespace(coords=coords)],
    )


def test_network_density_uses_unique_network_length() -> None:
    value = network_density({"unique_km": 100.0}, (50.0, 30.0, 51.0, 31.0))
    assert value > 0


def test_route_fully_in_bbox() -> None:
    route = _route("Поезд", [(50.5, 30.5), (50.8, 30.8)])
    assert route_fully_in_bbox(route, (50.0, 30.0, 51.0, 31.0))
    assert not route_fully_in_bbox(route, (50.0, 30.0, 50.7, 31.0))


def test_summary_type_rows_counts_transport_types() -> None:
    routes = [
        _route("Трамвай", [(50.0, 30.0), (50.1, 30.1)]),
        _route("Трамвай", [(50.0, 30.0), (50.1, 30.1)]),
        _route("Троллейбус", [(50.0, 30.0), (50.1, 30.1)]),
        _route("Поезд", [(50.0, 30.0), (50.1, 30.1)]),
    ]

    rows = summary_type_rows(routes, (49.0, 29.0, 51.0, 31.0))
    as_dict = dict(rows)
    assert as_dict["Трамваев"] == 2
    assert as_dict["Троллейбусов"] == 1
    assert as_dict["Поездов"] == 1
