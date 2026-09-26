from transit_planner.geo import Point
from transit_planner.network import (
    Network, Route, Service, ServicePeriod, Stop, TransitMode, VehicleType,
)
from transit_planner.serialization import dumps_network, loads_network


def test_round_trip_preserves_network():
    network = Network()
    network.add_stop(Stop("a", "A", Point(10, 20)))
    network.add_stop(Stop("b", "B", Point(11, 21), True))
    network.add_vehicle_type(VehicleType("tram", "Tram", TransitMode.TRAM, 200))
    network.add_period(ServicePeriod("day", 540, 960))
    network.add_route(Route("r1", "1", TransitMode.TRAM, ("a", "b")))
    network.add_service(Service("svc", "r1", "tram", {"day": 8}, {"day": 545.0}))

    restored = loads_network(dumps_network(network))

    assert restored.stops["b"].is_station
    assert restored.routes["r1"].mode == TransitMode.TRAM
    assert restored.services["svc"].headway_by_period["day"] == 8
    assert restored.services["svc"].departure_offset_by_period["day"] == 545.0
