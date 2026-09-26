from transit_planner.assignment import AssignmentConfig, assign_demand
from transit_planner.city import DemandZone
from transit_planner.demand import DemandMatrix, ODPairDemand
from transit_planner.geo import Point
from transit_planner.mode_choice import ModeChoiceConfig
from transit_planner.network import Network, ServicePeriod, Stop


def test_assignment_includes_bike_in_mode_split():
    network = Network()
    network.add_stop(Stop("a", "A", Point(0, 0)))
    network.add_stop(Stop("b", "B", Point(1000, 0)))
    network.add_period(ServicePeriod("peak", 0, 60))

    demand = DemandMatrix((
        ODPairDemand("z1", "z2", 100.0),
    ))

    result = assign_demand(
        network,
        demand,
        zones={
            "z1": DemandZone("z1", 0, 0, population=1000),
            "z2": DemandZone("z2", 1000, 0, population=1000),
        },
        config=AssignmentConfig(
            period_id="peak",
            max_access_distance_m=0,
            mode_choice=ModeChoiceConfig(
                scale=0.01,
                bike_constant=0.2,
            ),
        ),
    )

    total = (
        result.metrics.transit_trips
        + result.metrics.car_trips
        + result.metrics.walk_trips
        + result.metrics.bike_trips
    )
    assert abs(total - 100.0) < 1e-9
    assert result.metrics.bike_trips > 0
