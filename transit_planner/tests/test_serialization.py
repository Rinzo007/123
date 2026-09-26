from transit_planner.geo import Point
from transit_planner.infrastructure import TrackSection
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
    network.add_track_section(TrackSection("track-1", 1.0, speed_limit_kph=12.5))
    network.add_route(
        Route("r1", "1", TransitMode.TRAM, ("a", "b"), track_section_ids=("track-1",))
    )
    network.add_service(Service("svc", "r1", "tram", {"day": 8}, {"day": 545.0}))

    restored = loads_network(dumps_network(network))

    assert restored.stops["b"].is_station
    assert restored.routes["r1"].mode == TransitMode.TRAM
    assert restored.routes["r1"].track_section_ids == ("track-1",)
    assert restored.track_sections["track-1"].length_km == 1.0
    assert restored.track_sections["track-1"].speed_limit_kph == 12.5
    assert restored.services["svc"].headway_by_period["day"] == 8
    assert restored.services["svc"].departure_offset_by_period["day"] == 545.0


def test_serialization_round_trips_reference_route_rows_and_service_phase():
    from transit_planner.reference_model import TrackRow

    network = Network()
    network.add_stop(Stop("a", "A", Point(0, 0)))
    network.add_stop(Stop("b", "B", Point(1000, 0)))
    network.add_stop(Stop("c", "C", Point(2000, 0)))
    network.add_vehicle_type(VehicleType("tram", "Tram", TransitMode.TRAM, 250))
    network.add_period(ServicePeriod("am", 360, 540))
    network.add_route(Route("r1", "1", TransitMode.TRAM, ("a", "b", "c"), row_by_segment=(TrackRow.RESERVED, TrackRow.GRADE)))
    network.add_service(Service("svc", "r1", "tram", {"am": 10}, phase_by_period={"am": 12.0}))
    restored = network_from_dict(network_to_dict(network))
    assert restored.routes["r1"].row_by_segment == (TrackRow.RESERVED, TrackRow.GRADE)
    assert restored.services["svc"].phase_by_period == {"am": 12.0}
