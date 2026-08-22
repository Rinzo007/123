from types import SimpleNamespace

from wikiroutes.enums import RouteType
from wikiroutes.pipeline_loading import build_route_tasks


def _catalog():
    link_bus = SimpleNamespace(name="10", route_id=10)
    link_tram = SimpleNamespace(name="1", route_id=1)
    return SimpleNamespace(
        sections=[
            SimpleNamespace(route_type=RouteType.BUS, links=[link_bus]),
            SimpleNamespace(route_type=RouteType.TRAM, links=[link_tram]),
        ]
    )


def _config(**overrides):
    values = {
        "route_filter": None,
        "max_route_number": 0,
        "disabled_types": set(),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_all_route_types_are_loaded_as_one_group():
    tasks = build_route_tasks(_catalog(), "voronezh", _config())

    assert [(task.route_type, task.name) for task in tasks] == [
        (RouteType.BUS, "10"),
        (RouteType.TRAM, "1"),
    ]


def test_disabled_type_is_skipped():
    tasks = build_route_tasks(
        _catalog(),
        "voronezh",
        _config(disabled_types={RouteType.BUS}),
    )

    assert [(task.route_type, task.name) for task in tasks] == [(RouteType.TRAM, "1")]


def test_route_filter_applies_to_all_types():
    tasks = build_route_tasks(
        _catalog(),
        "voronezh",
        _config(route_filter="1"),
    )

    assert [(task.route_type, task.name) for task in tasks] == [(RouteType.TRAM, "1")]
