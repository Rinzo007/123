from transit_planner.geo import Point
from transit_planner.network import (
    Network, Route, Service, ServicePeriod, Stop, TransitMode, VehicleType,
)


def make_network() -> Network:
    network = Network()
    network.add_stop(Stop("a", "A", Point(0, 0)))
    network.add_stop(Stop("b", "B", Point(1, 0)))
    network.add_vehicle_type(VehicleType("bus", "Bus", TransitMode.BUS, 80))
    network.add_period(ServicePeriod("peak", 360, 540))
    network.add_route(Route("r1", "1", TransitMode.BUS, ("a", "b")))
    network.add_service(Service("svc", "r1", "bus", {"peak": 10}))
    return network


def test_network_is_valid():
    assert make_network().validate() == []


def test_missing_stop_is_rejected():
    network = Network()
    network.add_stop(Stop("a", "A", Point(0, 0)))
    try:
        network.add_route(Route("broken", "Broken", TransitMode.BUS, ("a", "missing")))
    except ValueError as exc:
        assert "unknown stops" in str(exc)
    else:
        raise AssertionError("Expected ValueError")


def test_vehicle_mode_must_match_route_mode():
    network = Network()
    network.add_stop(Stop("a", "A", Point(0, 0)))
    network.add_stop(Stop("b", "B", Point(1, 0)))
    network.add_vehicle_type(VehicleType("tram", "Tram", TransitMode.TRAM, 200))
    network.add_period(ServicePeriod("day", 0, 1440))
    network.add_route(Route("r1", "1", TransitMode.BUS, ("a", "b")))
    try:
        network.add_service(Service("svc", "r1", "tram", {"day": 10}))
    except ValueError as exc:
        assert "vehicle mode" in str(exc)
    else:
        raise AssertionError("Expected ValueError")


def test_default_service_periods_match_reference_model():
    from transit_planner.reference_model import REFERENCE_PERIODS

    actual = [
        (period.id, period.start_minute, period.end_minute)
        for period in __import__("transit_planner.network", fromlist=["default_service_periods"]).default_service_periods()
    ]
    expected = [
        (period.key, period.start_minute, period.end_minute)
        for period in REFERENCE_PERIODS
    ]
    assert actual == expected
