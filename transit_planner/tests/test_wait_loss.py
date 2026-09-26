from transit_planner.assignment import AssignmentConfig, assign_demand
from transit_planner.choice import ChoiceConfig
from transit_planner.demand import DemandMatrix, ODPairDemand
from transit_planner.geo import Point
from transit_planner.network import Network, Route, Service, ServicePeriod, Stop, TransitMode, VehicleType


def test_wait_is_tracked_separately_from_transit_runtime():
    network = Network()
    network.add_stop(Stop("a", "A", Point(0, 0)))
    network.add_stop(Stop("b", "B", Point(1000, 0)))
    network.add_vehicle_type(VehicleType("bus", "Bus", TransitMode.BUS, 90))
    network.add_period(ServicePeriod("am", 360, 540))
    network.add_route(Route("r1", "1", TransitMode.BUS, ("a", "b")))
    network.add_service(Service("svc", "r1", "bus", {"am": 60}, {"am": 0}))

    result = assign_demand(
        network,
        DemandMatrix((ODPairDemand("a", "b", 100.0),)),
        config=AssignmentConfig(
            period_id="am",
            max_access_distance_m=0,
            choice=ChoiceConfig(
                value_of_time_s_per_eur=360.0,
                transit_constant=0.5,
                car_constant=-0.5,
                walk_constant=-3.0,
                bike_constant=-3.0,
            ),
        ),
    )

    assert result.loss_reasons
    assert any(loss.reason == "wait" for loss in result.loss_reasons)
