from transit_planner.assignment import AssignmentConfig, assign_demand
from transit_planner.city import DemandZone
from transit_planner.demand import DemandMatrix, ODPairDemand
from transit_planner.geo import Point
from transit_planner.choice import ChoiceConfig, utilities
from transit_planner.network import Network, ServicePeriod, Stop


def test_reference_choice_penalizes_wait_and_long_bike_trips():
    config = ChoiceConfig()
    no_wait = utilities(
        walk_time_min=20.0,
        car_time_min=10.0,
        transit_time_min=8.0,
        transit_wait_min=0.0,
        bike_time_min=12.0,
        car_distance_km=5.0,
        bike_distance_km=5.0,
        config=config,
    )
    long_bike = utilities(
        walk_time_min=20.0,
        car_time_min=10.0,
        transit_time_min=8.0,
        bike_time_min=12.0,
        car_distance_km=5.0,
        bike_distance_km=8.0,
        config=config,
    )
    with_wait = utilities(
        walk_time_min=20.0,
        car_time_min=10.0,
        transit_time_min=8.0,
        transit_wait_min=10.0,
        bike_time_min=12.0,
        car_distance_km=5.0,
        bike_distance_km=5.0,
        config=config,
    )
    assert with_wait.transit < no_wait.transit
    assert long_bike.bike == float("-inf")


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
            choice=ChoiceConfig(
                value_of_time_s_per_eur=360.0,
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


def test_alternative_probabilities_favor_lower_generalized_cost():
    from transit_planner.choice import alternative_probabilities

    shares = alternative_probabilities(((10.0, 5.0), (14.0, 8.0)))
    assert abs(sum(shares) - 1.0) < 1e-12
    assert shares[0] > shares[1] > 0.0


def test_no_car_availability_reduces_car_probability():
    from transit_planner.choice import probabilities

    utilities_value = utilities(
        walk_time_min=20.0,
        car_time_min=10.0,
        transit_time_min=None,
    )
    with_car = probabilities(utilities_value, car_availability=1.0)
    without_car = probabilities(utilities_value, car_availability=0.0)
    assert without_car["car"] == 0.0
    assert without_car["walk"] > with_car["walk"]


def test_trip_suppression_factor_matches_vot_exponential():
    from transit_planner.choice import trip_suppression_factor

    assert trip_suppression_factor(0.0) == 1.0
    assert 0.0 < trip_suppression_factor(6.0) < 1.0
    assert trip_suppression_factor(12.0) < trip_suppression_factor(6.0)
