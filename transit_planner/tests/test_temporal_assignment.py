from transit_planner.assignment import AssignmentConfig
from transit_planner.demand import PeriodODPairDemand, TemporalDemandMatrix
from transit_planner.network import Network, Route, Service, ServicePeriod, Stop, TransitMode, VehicleType
from transit_planner.temporal_assignment import assign_temporal_demand
from transit_planner.geo import Point


def build_network() -> Network:
    network = Network()
    network.add_stop(Stop("a", "A", Point(0, 0)))
    network.add_stop(Stop("b", "B", Point(1000, 0)))
    network.add_vehicle_type(VehicleType("bus", "Bus", TransitMode.BUS, 80))
    network.add_period(ServicePeriod("morning_peak", 420, 540))
    network.add_period(ServicePeriod("evening_peak", 960, 1080))
    network.add_route(Route("r1", "1", TransitMode.BUS, ("a", "b")))
    network.add_service(Service("svc", "r1", "bus", {
        "morning_peak": 10,
        "evening_peak": 15,
    }))
    return network


def test_assign_temporal_demand_returns_one_assignment_per_network_period():
    network = build_network()
    demand = TemporalDemandMatrix((
        PeriodODPairDemand("z1", "z2", "morning_peak", 100.0, "work"),
        PeriodODPairDemand("z1", "z2", "evening_peak", 50.0, "work"),
    ))

    result = assign_temporal_demand(
        network,
        demand,
        config=AssignmentConfig(
            period_id="morning_peak",
        ),
    )

    assert [item.period_id for item in result.periods] == [
        "morning_peak",
        "evening_peak",
    ]
    assert abs(result.total_demand_trips - 150.0) < 1e-9


def test_temporal_totals_sum_mode_results():
    network = build_network()
    demand = TemporalDemandMatrix((
        PeriodODPairDemand("z1", "z2", "morning_peak", 10.0, "work"),
        PeriodODPairDemand("z1", "z2", "evening_peak", 20.0, "work"),
    ))

    result = assign_temporal_demand(
        network,
        demand,
        config=AssignmentConfig(period_id="morning_peak"),
    )

    assert (
        result.total_transit_trips
        + result.total_car_trips
        + result.total_walk_trips
        + result.total_bike_trips
    ) == result.total_demand_trips
