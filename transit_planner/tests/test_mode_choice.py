"""Выбор режима: в игре ровно три режима - транзит, авто, пешком.

Велосипед, доля без автомобиля и `rest` («остаться дома») в игре
отсутствуют, поэтому в сплите их нет. Спрос, не реализованный поездкой, в
выбор режима не входит и в метриках не учитывается.
"""
from transit_planner.assignment import AssignmentConfig, assign_demand
from transit_planner.city import DemandZone
from transit_planner.choice import (
    ChoiceConfig,
    alternative_probabilities,
    probabilities,
    utilities,
)
from transit_planner.demand import DemandMatrix, ODPairDemand
from transit_planner.geo import Point
from transit_planner.network import Network, ServicePeriod, Stop


def test_reference_choice_penalizes_wait():
    config = ChoiceConfig()
    no_wait = utilities(
        walk_time_min=20.0,
        car_time_min=10.0,
        transit_time_min=8.0,
        transit_wait_min=0.0,
        car_distance_km=5.0,
        config=config,
    )
    with_wait = utilities(
        walk_time_min=20.0,
        car_time_min=10.0,
        transit_time_min=8.0,
        transit_wait_min=10.0,
        car_distance_km=5.0,
        config=config,
    )
    assert with_wait.transit < no_wait.transit


def test_assignment_covers_every_trip_across_three_modes():
    network = Network()
    network.add_stop(Stop("a", "A", Point(0, 0)))
    network.add_stop(Stop("b", "B", Point(1000, 0)))
    network.add_period(ServicePeriod("peak", 0, 60))

    demand = DemandMatrix((ODPairDemand("z1", "z2", 100.0),))

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
            choice=ChoiceConfig(value_of_time_s_per_eur=360.0),
        ),
    )

    total = (
        result.metrics.transit_trips
        + result.metrics.car_trips
        + result.metrics.walk_trips
    )
    assert abs(total - 100.0) < 1e-9


def test_alternative_probabilities_favor_lower_generalized_cost():
    shares = alternative_probabilities((15.0, 22.0))
    assert abs(sum(shares) - 1.0) < 1e-12
    assert shares[0] > shares[1] > 0.0


def test_choice_defaults_match_reference_active_mode_parameters():
    config = ChoiceConfig()
    assert config.walk_circuity == 1.33
    assert config.car_circuity == 1.3


def test_reference_mode_split_sums_to_one():
    """Сплит по логиту даёт ровно три режима: transit/car/walk."""
    values = utilities(
        walk_time_min=20.0,
        car_time_min=10.0,
        transit_time_min=8.0,
    )
    shares = probabilities(values)
    assert set(shares) == {"transit", "car", "walk"}
    assert abs(sum(shares.values()) - 1.0) < 1e-12


def test_unavailable_transit_drops_out_of_the_split():
    values = utilities(
        walk_time_min=20.0,
        car_time_min=10.0,
        transit_time_min=None,
    )
    shares = probabilities(values)
    assert shares["transit"] == 0.0
    assert shares["walk"] > 0.0
    assert shares["car"] > 0.0
