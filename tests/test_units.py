from __future__ import annotations

from wikiroutes.enums import RouteType
from wikiroutes.models import Direction, RouteData
from wikiroutes.units import DirectionKey, build_units


def _route(route_id: int, name: str) -> RouteData:
    direction_a = Direction(
        coords=((51.67, 39.20), (51.68, 39.21)),
        stops=(),
    )
    direction_b = Direction(
        coords=((51.68, 39.21), (51.69, 39.22)),
        stops=(),
    )
    return RouteData(
        name=name,
        route_type=RouteType.BUS,
        route_id=route_id,
        url="",
        directions=(direction_a, direction_b),
    )


def test_direction_key_is_stable_and_typed() -> None:
    units = build_units([_route(10, "10"), _route(20, "20")])

    assert [unit.key for unit in units] == [
        (10, 0),
        (10, 1),
        (20, 0),
        (20, 1),
    ]
    assert all(isinstance(unit.key, tuple) for unit in units)
    assert all(len(unit.key) == 2 for unit in units)


def test_direction_key_does_not_depend_on_route_order() -> None:
    forward = build_units([_route(10, "10"), _route(20, "20")])
    reverse = build_units([_route(20, "20"), _route(10, "10")])

    forward_keys = {unit.key for unit in forward}
    reverse_keys = {unit.key for unit in reverse}

    assert forward_keys == reverse_keys
    assert forward_keys == {
        (10, 0),
        (10, 1),
        (20, 0),
        (20, 1),
    }


def test_legacy_unit_id_remains_local_index() -> None:
    units = build_units([_route(10, "10"), _route(20, "20")])

    assert [unit.unit_id for unit in units] == [0, 1, 2, 3]
    assert all(isinstance(unit.key, DirectionKey.__args__) for unit in units)
