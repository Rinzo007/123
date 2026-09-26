from transit_planner.city import DemandZone
from transit_planner.demand import DemandMatrix
from transit_planner.geo import Point
from transit_planner.network import (
    Network, Route, Service, ServicePeriod, Stop, TransitMode, VehicleType,
)
from transit_planner.od import GravityParameters, gravity_od
from transit_planner.routing import RouterConfig, TransitRouter
from transit_planner.snap import snap_stops_to_road_graph
from transit_planner.road import RoadEdge, RoadGraph, RoadNode
from transit_planner.zones import generate_grid_zones


def test_grid_zones_aggregate_population_and_jobs():
    zones = generate_grid_zones(
        0, 0, 200, 200,
        cell_size=100,
        population_points=((10, 10, 100), (150, 150, 50)),
        job_points=((10, 10, 40),),
    )
    assert len(zones) == 4
    z00 = next(z for z in zones if z.id == "z0000_0000")
    assert z00.population == 100
    assert z00.jobs == 40


def test_gravity_od_balances_to_origin_production():
    zones = (
        DemandZone("a", 0, 0, population=1000, jobs=100),
        DemandZone("b", 1000, 0, population=500, jobs=900),
    )
    demand = gravity_od(
        zones,
        parameters=GravityParameters(impedance_minutes=20, decay=0.01),
        trip_rate=0.1,
    )
    assert isinstance(demand, DemandMatrix)
    assert abs(demand.total_trips_per_day - 150.0) < 1e-9


def test_stop_snapping_uses_nearest_road_node():
    graph = RoadGraph()
    graph.add_node(RoadNode(1, 0, 0))
    graph.add_node(RoadNode(2, 100, 0))
    graph.add_edge(RoadEdge("e", 1, 2, 100, 30))

    stops = (
        Stop("s1", "S1", Point(8, 4)),
        Stop("s2", "S2", Point(92, 3)),
    )
    snapped = snap_stops_to_road_graph(stops, graph, cell_size=50)
    assert snapped[0].road_node_id == 1
    assert snapped[1].road_node_id == 2


def test_router_waits_once_on_continuation():
    network = Network()
    for stop_id, x in (("a", 0), ("b", 1000), ("c", 2000)):
        network.add_stop(Stop(stop_id, stop_id.upper(), Point(x, 0)))
    network.add_vehicle_type(VehicleType("bus", "Bus", TransitMode.BUS, 80))
    network.add_period(ServicePeriod("peak", 0, 60))
    network.add_route(Route("r1", "1", TransitMode.BUS, ("a", "b", "c")))
    network.add_service(Service("svc", "r1", "bus", {"peak": 10}))

    journey = TransitRouter(
        network, config=RouterConfig(walk_transfer_radius_m=0)
    ).shortest(network.stops["a"], network.stops["c"], period_id="peak")

    assert journey is not None
    assert journey.transfers == 0
    assert len(journey.legs) == 2
    # 5 min wait + 2 km at 20 km/h = 11 min.
    assert abs(journey.duration_min - 11.0) < 1e-9
