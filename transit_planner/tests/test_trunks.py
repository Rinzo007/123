from transit_planner.geo import Point
from transit_planner.network import Network, Route, Service, ServicePeriod, Stop, TransitMode, VehicleType
from transit_planner.trunks import Trunk, detect_shared_trunks


def make_network(routes: dict[str, tuple[str, ...]], closed: bool = False) -> Network:
    network = Network()
    stops: set[str] = set()
    for stop_ids in routes.values():
        stops.update(stop_ids)
    for index, stop_id in enumerate(sorted(stops)):
        network.add_stop(Stop(stop_id, stop_id.upper(), Point(float(index * 1000), 0.0)))
    network.add_vehicle_type(VehicleType("bus", "Bus", TransitMode.BUS, 80))
    network.add_period(ServicePeriod("am", 360, 540))
    for route_id, stop_ids in routes.items():
        network.add_route(Route(route_id, route_id, TransitMode.BUS, stop_ids, closed=closed))
        network.add_service(Service(f"svc-{route_id}", route_id, "bus", {"am": 10}))
    return network


def test_shared_trunk_for_parallel_routes() -> None:
    network = make_network({"r1": ("a", "b", "c", "d"), "r2": ("a", "b", "c", "d")})

    trunks = detect_shared_trunks(network, min_pair_overlap=1)

    assert len(trunks) == 1
    trunk = trunks[0]
    assert trunk.route_ids == ("r1", "r2")
    assert trunk.segment_ids
    assert trunk.length_km > 0.0
    assert trunk.max_pair_overlap >= 1


def test_no_trunk_for_disjoint_routes() -> None:
    network = make_network({"r1": ("a", "b"), "r2": ("c", "d")})

    assert detect_shared_trunks(network) == ()


def test_partial_overlap_needs_minimum_pairs() -> None:
    network = make_network({"r1": ("a", "b", "c"), "r2": ("a", "b")})

    assert detect_shared_trunks(network, min_pair_overlap=1)
    assert detect_shared_trunks(network, min_pair_overlap=3) == ()


def test_bearing_gate_filters_opposite_direction_routes() -> None:
    network = make_network({"r1": ("a", "b", "c", "d"), "r2": ("d", "c", "b", "a")})

    assert detect_shared_trunks(network, min_pair_overlap=1, max_bearing_deg=10.0) == ()


def test_validation_rejects_bad_thresholds() -> None:
    network = make_network({"r1": ("a", "b")})

    for kwargs in ({"max_bearing_deg": -1.0}, {"min_overlap_ratio": 1.5}, {"min_pair_overlap": 0}):
        try:
            detect_shared_trunks(network, **kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(f"ожидался ValueError для {kwargs}")
