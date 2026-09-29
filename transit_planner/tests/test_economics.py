from transit_planner.assignment import AssignmentConfig, assign_demand
from transit_planner.city import DemandZone
from transit_planner.demand import DemandMatrix, ODPairDemand
from transit_planner.economics import (
    EconomicsConfig,
    aggregate_temporal_economics,
    calculate_economics,
)
from transit_planner.geo import Point
from transit_planner.network import (
    Network, Route, Service, ServicePeriod, Stop, TransitMode, VehicleType,
)

# Без зон расстояние между ними подставляется фиктивно, и пеший вариант
# сравнивается с автобусом по неверной дистанции.
ZONES = {
    "a": DemandZone(id="a", centroid_x=0.0, centroid_y=0.0, population=100.0, jobs=0.0),
    "b": DemandZone(id="b", centroid_x=10_000.0, centroid_y=0.0, population=0.0, jobs=100.0),
}


def test_operating_economics():
    # 10 км: пешком это 111 минут при игровых 5.4 км/ч, то есть за пределами
    # 45-минутного лимита, и автобус выигрывает однозначно. На 1 км пешком
    # выигрывал автобус с 10-минутным интервалом - проверять экономику на
    # паре, где никто не едет, бессмысленно.
    network = Network()
    network.add_stop(Stop("a", "A", Point(0, 0)))
    network.add_stop(Stop("b", "B", Point(10_000, 0)))
    network.add_vehicle_type(VehicleType("bus", "Bus", TransitMode.BUS, 80, 2.0))
    network.add_period(ServicePeriod("peak", 0, 60))
    network.add_route(Route("r1", "1", TransitMode.BUS, ("a", "b")))
    network.add_service(Service("svc", "r1", "bus", {"peak": 10}))

    assignment = assign_demand(
        network,
        DemandMatrix((ODPairDemand("a", "b", 100),)),
        config=AssignmentConfig(period_id="peak", max_access_distance_m=0),
        zones=ZONES,
    )
    result = calculate_economics(
        network,
        assignment,
        config=EconomicsConfig(period_id="peak", fare_per_transit_trip=2.0),
    )
    assert result.daily_vehicle_km == 120.0
    assert result.daily_fare_revenue > 0
    assert result.annual_operating_cost == result.daily_operating_cost * 365


def test_fleet_cost_is_zero_without_vehicle_acquisition_cost():
    network = Network()
    network.add_stop(Stop("a", "A", Point(0, 0)))
    network.add_stop(Stop("b", "B", Point(1000, 0)))
    network.add_vehicle_type(VehicleType("bus", "Bus", TransitMode.BUS, 80))
    network.add_period(ServicePeriod("peak", 0, 60))
    network.add_route(Route("r1", "1", TransitMode.BUS, ("a", "b")))
    network.add_service(Service("svc", "r1", "bus", {"peak": 10}))

    assignment = assign_demand(
        network,
        DemandMatrix((ODPairDemand("a", "b", 10),)),
        config=AssignmentConfig(period_id="peak", max_access_distance_m=0),
    )
    result = calculate_economics(
        network,
        assignment,
        config=EconomicsConfig(period_id="peak"),
    )

    assert result.daily_fleet_cost == 0.0
    assert result.annual_fleet_cost == 0.0


