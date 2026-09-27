from transit_planner.choice import ChoiceConfig, utilities
from transit_planner.geo import Point
from transit_planner.network import (
    Network, Route, Service, ServicePeriod, Stop, TransitMode, VehicleType,
)
from transit_planner.reference_model import REFERENCE_TRANSIT_BURDENS
from transit_planner.routing import RouterConfig, TransitRouter


def test_ha2020_transfer_burdens_are_nonlinear():
    config = ChoiceConfig()
    assert config.transfer_burden_minutes(0) == 0.0
    assert config.transfer_burden_minutes(1) == REFERENCE_TRANSIT_BURDENS.first_transfer_burden_min
    assert config.transfer_burden_minutes(2) == REFERENCE_TRANSIT_BURDENS.multiple_transfer_burden_min
    assert config.transfer_burden_minutes(5) == config.transfer_burden_minutes(2)
    assert config.transfer_burden_minutes(2) > 3 * config.transfer_burden_minutes(1)


def test_stage_weighted_walk_penalizes_egress_more_than_access():
    config = ChoiceConfig()
    assert config.transit_stage_egress_weight > config.transit_stage_access_weight
    assert config.transit_stage_transfer_walk_weight > config.transit_stage_egress_weight
    base = utilities(
        walk_time_min=15.0,
        car_time_min=10.0,
        transit_time_min=8.0,
    )
    access = utilities(
        walk_time_min=15.0,
        car_time_min=10.0,
        transit_time_min=8.0,
        transit_access_walk_min=5.0,
        config=config,
    )
    egress = utilities(
        walk_time_min=15.0,
        car_time_min=10.0,
        transit_time_min=8.0,
        transit_egress_walk_min=5.0,
        config=config,
    )
    assert egress.transit < access.transit < base.transit


def test_two_transfer_trip_is_penalized_in_mode_choice():
    config = ChoiceConfig()
    one = utilities(
        walk_time_min=15.0,
        car_time_min=10.0,
        transit_time_min=8.0,
        transit_transfers=1,
        config=config,
    )
    two = utilities(
        walk_time_min=15.0,
        car_time_min=10.0,
        transit_time_min=8.0,
        transit_transfers=2,
        config=config,
    )
    assert two.transit < one.transit


def test_raptor_carries_labels_through_untouched_rounds():
    network = Network()
    for stop_id, x in (
        ("a", 0),
        ("p", 3000),
        ("q", 6000),
        ("z", 9000),
    ):
        network.add_stop(Stop(stop_id, stop_id.upper(), Point(x, 0)))
    network.add_vehicle_type(VehicleType("bus", "Bus", TransitMode.BUS, 90))
    network.add_period(ServicePeriod("am", 360, 540))
    network.add_route(Route("first", "1", TransitMode.BUS, ("a", "p")))
    network.add_route(Route("second", "2", TransitMode.BUS, ("q", "z")))
    network.add_service(Service("first-service", "first", "bus", {"am": 10}))
    network.add_service(Service("second-service", "second", "bus", {"am": 10}))

    router = TransitRouter(
        network,
        config=RouterConfig(
            walk_transfer_radius_m=3500,
            raptor_max_transfers=2,
        ),
    )
    journey = router.shortest(network.stops["a"], network.stops["z"], period_id="am")

    assert journey is not None
    assert journey.transfers == 1
    transit_routes = [leg.route_id for leg in journey.legs if leg.kind == "transit"]
    assert transit_routes == ["first", "second"]
