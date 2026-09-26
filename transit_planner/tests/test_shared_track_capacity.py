from transit_planner.assignment import _section_capacity_and_platforms
from transit_planner.infrastructure import TrackSection
from transit_planner.network import (
    Network,
    Route,
    Service,
    ServicePeriod,
    Stop,
    TransitMode,
    VehicleType,
)
from transit_planner.geo import Point


def test_shared_track_limits_combined_service_departures():
    network = Network()
    for stop_id, x in (("a", 0), ("b", 1000), ("c", 0), ("d", 1000)):
        network.add_stop(Stop(stop_id, stop_id.upper(), Point(x, 0 if stop_id in ("a", "b") else 100)))
    network.add_vehicle_type(VehicleType("tram", "Tram", TransitMode.TRAM, 250))
    network.add_period(ServicePeriod("am", 360, 540))
    network.add_track_section(
        TrackSection(
            "shared-1",
            1.0,
            capacity_departures_per_hour=20,
            shared_group="corridor-1",
        )
    )
    network.add_track_section(
        TrackSection(
            "shared-2",
            1.0,
            capacity_departures_per_hour=20,
            shared_group="corridor-1",
        )
    )
    network.add_route(Route("r1", "T1", TransitMode.TRAM, ("a", "b"), track_section_ids=("shared-1",)))
    network.add_route(Route("r2", "T2", TransitMode.TRAM, ("c", "d"), track_section_ids=("shared-2",)))
    network.add_service(Service("s1", "r1", "tram", {"am": 15}))
    network.add_service(Service("s2", "r2", "tram", {"am": 15}))

    sections, _ = _section_capacity_and_platforms(network, "am")

    capacities = {item[:3]: item[3] for item in sections}
    assert capacities[("r1", "a", "b")] == 7500.0
    assert capacities[("r2", "c", "d")] == 7500.0


def test_one_way_route_has_no_reverse_capacity() -> None:
    network = Network()
    for stop_id, x in (("a", 0), ("b", 1000)):
        network.add_stop(Stop(stop_id, stop_id.upper(), Point(x, 0)))
    network.add_vehicle_type(VehicleType("bus", "Bus", TransitMode.BUS, 90))
    network.add_period(ServicePeriod("am", 360, 540))
    network.add_route(
        Route(
            "ow",
            "OW",
            TransitMode.BUS,
            ("a", "b"),
            both_ways=False,
        )
    )
    network.add_service(Service("ow-service", "ow", "bus", {"am": 10}))

    sections, _ = _section_capacity_and_platforms(network, "am")
    keys = {item[:3] for item in sections}
    assert ("ow", "a", "b") in keys
    assert ("ow", "b", "a") not in keys
