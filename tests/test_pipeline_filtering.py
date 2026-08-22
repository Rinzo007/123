from types import SimpleNamespace

from wikiroutes.enums import RouteType
from wikiroutes.pipeline_filtering import (
    build_filter_limits,
    split_active_routes,
    split_ok_and_errors,
)


def _route(*, active=True, error=None, directions=True):
    return SimpleNamespace(
        active=active,
        error=error,
        directions=([object()] if directions else []),
        route_type=RouteType.BUS,
        curvilinearity=1.0,
        min_km=1.0,
    )


def _config(**overrides):
    values = {
        "curv": None,
        "minlen": None,
        "radius": None,
        "center_lat": None,
        "center_lon": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_build_filter_limits_defaults_to_zero_without_radius_center():
    limits = build_filter_limits(_config())

    assert limits.curvilinearity == 0.0
    assert limits.min_length_km == 0.0
    assert limits.radius_km == 0.0
    assert limits.center_lat is None
    assert limits.center_lon is None


def test_radius_is_disabled_without_both_center_coordinates():
    limits = build_filter_limits(_config(radius=10.0, center_lat=51.0))

    assert limits.radius_km == 0.0
    assert limits.center_lat is None
    assert limits.center_lon is None


def test_split_active_routes():
    active = _route(active=True)
    inactive = _route(active=False)

    routes, skipped = split_active_routes([active, inactive], active_only=True)

    assert routes == [active]
    assert skipped == 1


def test_split_ok_and_errors():
    ok = _route()
    bad = _route(error="download failed")
    empty = _route(directions=False)

    good, errors = split_ok_and_errors([ok, bad, empty])

    assert good == [ok]
    assert errors == [bad]
