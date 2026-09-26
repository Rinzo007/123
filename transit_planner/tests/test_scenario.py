from transit_planner.assignment import AssignmentConfig
from transit_planner.demand import DemandMatrix, ODPairDemand
from transit_planner.geo import Point
from transit_planner.network import (
    Network, Route, Service, ServicePeriod, Stop, TransitMode, VehicleType,
)
from transit_planner.scenario import (
    ScenarioDefinition,
    compare_scenarios,
    run_scenario,
)


def network(headway: float) -> Network:
    n = Network()
    n.add_stop(Stop("a", "A", Point(0, 0)))
    n.add_stop(Stop("b", "B", Point(1000, 0)))
    n.add_vehicle_type(VehicleType("bus", "Bus", TransitMode.BUS, 80))
    n.add_period(ServicePeriod("peak", 0, 60))
    n.add_route(Route("r1", "1", TransitMode.BUS, ("a", "b")))
    n.add_service(Service("svc", "r1", "bus", {"peak": headway}))
    return n


def definition(scenario_id: str, headway: float) -> ScenarioDefinition:
    return ScenarioDefinition(
        id=scenario_id,
        name=scenario_id,
        network=network(headway),
        demand=DemandMatrix((ODPairDemand("a", "b", 100),)),
        assignment_config=AssignmentConfig(
            period_id="peak",
            max_access_distance_m=0,
            iterations=2,
        ),
    )


def test_scenarios_run_and_compare_without_ranking():
    base = run_scenario(definition("base", 10))
    alt = run_scenario(definition("alt", 5))
    comparison = compare_scenarios(base, alt)

    transit_share = next(
        item for item in comparison.metrics if item.metric == "transit_share"
    )
    assert transit_share.base >= 0
    assert transit_share.alternative >= 0
    assert len(comparison.sections) == 2
