from overture.ports import CachePort, DirectionPort, RoutePort, StatsPort


def test_ports_are_protocols():
    assert getattr(CachePort, "_is_protocol", False)
    assert getattr(DirectionPort, "_is_protocol", False)
    assert getattr(RoutePort, "_is_protocol", False)
    assert getattr(StatsPort, "_is_protocol", False)
