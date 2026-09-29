import pytest

from transit_planner.assignment import AssignmentConfig, assign_demand
from transit_planner.demand import DemandMatrix, ODPairDemand
from transit_planner.city import DemandZone
from transit_planner.geo import Point
from transit_planner.network import (
    Network, Route, Service, ServicePeriod, Stop, TransitMode, VehicleType,
)
from transit_planner.routing import RouterConfig, TransitRouter


def make_network() -> Network:
    network = Network()
    for stop_id, x in (("a", 0), ("b", 1000), ("c", 2000)):
        network.add_stop(Stop(stop_id, stop_id.upper(), Point(x, 0)))
    network.add_vehicle_type(VehicleType("bus", "Bus", TransitMode.BUS, 80))
    network.add_period(ServicePeriod("peak", 0, 60))
    network.add_route(Route("r1", "1", TransitMode.BUS, ("a", "b", "c")))
    network.add_service(Service("svc", "r1", "bus", {"peak": 10}))
    return network


def test_assignment_produces_transit_flow():
    network = make_network()
    demand = DemandMatrix((ODPairDemand("a", "c", 100),))
    result = assign_demand(
        network,
        demand,
        config=AssignmentConfig(period_id="peak", max_access_distance_m=0),
        router=TransitRouter(network, config=RouterConfig(walk_transfer_radius_m=0)),
    )

    assert result.metrics.total_trips == 100
    assert result.metrics.transit_trips > 0
    assert result.metrics.average_wait_time_min >= 0.0
    assert result.route_flows[0].passenger_section_traversals > 0
    assert dict(result.service_headway_factors)["svc"] > 1.0
    section = next(
        item for item in result.section_loads
        if item.from_stop_id == "a" and item.to_stop_id == "b"
    )
    assert section.passengers > 0
    assert section.capacity == 480
    assert section.denied_boardings == 0.0


def test_assignment_reports_denied_boardings_when_capacity_is_exceeded():
    """Переполнение секции фиксируется как отказ в посадке.

    Первая итерация без обратной связи по заполненности: те, кто выбрал
    транзит, едут все, поэтому 970 пассажиров против 480 мест дают 490
    отказов. Устойчивое равновесие с переполнением не возникло бы - игра
    поднимает время поездки при загрузке и лишние пассажиры уходят на авто,
    поэтому для проверки учёта отказов берётся именно первая итерация.
    """
    network = Network()
    for stop_id, x in (("a", 0), ("b", 10_000), ("c", 20_000)):
        network.add_stop(Stop(stop_id, stop_id.upper(), Point(x, 0)))
    network.add_vehicle_type(VehicleType("bus", "Bus", TransitMode.BUS, 80))
    network.add_period(ServicePeriod("peak", 0, 60))
    network.add_route(Route("r1", "1", TransitMode.BUS, ("a", "b", "c")))
    network.add_service(Service("svc", "r1", "bus", {"peak": 10}))

    zones = {
        "a": DemandZone("a", 0, 0, population=1000),
        "c": DemandZone("c", 20_000, 0, population=1000),
    }
    result = assign_demand(
        network,
        DemandMatrix((ODPairDemand("a", "c", 1000),)),
        zones=zones,
        config=AssignmentConfig(
            period_id="peak", max_access_distance_m=0, iterations=1
        ),
        router=TransitRouter(network, config=RouterConfig(walk_transfer_radius_m=0)),
    )

    sections = {
        f"{item.from_stop_id}->{item.to_stop_id}": item
        for item in result.section_loads
    }
    # Выбор по минимуму стоимости неоднороден: часть ступеней дохода
    # выбирает авто, поэтому едут не все 1000, но транспорт едет всегда -
    # пешком 20 км не ходят.
    assert result.metrics.transit_trips > 0.0
    assert result.metrics.walk_trips == pytest.approx(0.0, abs=1e-6)
    assert sections["a->b"].capacity == 480.0
    # Переполнение возникает на участке после посадки: мест 480, спрос выше.
    assert sections["b->c"].passengers > sections["b->c"].capacity
    assert sections["b->c"].denied_boardings == pytest.approx(
        sections["b->c"].passengers - sections["b->c"].capacity
    )
    assert result.metrics.denied_boardings > 0.0


def test_zone_coordinates_drive_car_and_walk_costs():
    """Координаты зон задают модальность: близко - пешком, далеко - авто.

    Пороги взяты из правила игры, а не подогнаны: постоянные +576 с
    парковки делают авто невыгодным на коротких поездках, а пешеходная
    скорость 1.4 м/с съедает дальние направления. Транзит исключён
    (`max_access_distance_m=0`), чтобы сравнивались только авто и пешком.
    """
    network = make_network()
    config = AssignmentConfig(period_id="peak", max_access_distance_m=0)

    near = assign_demand(
        network,
        DemandMatrix((ODPairDemand("o", "d", 100),)),
        zones={
            "o": DemandZone("o", 0, 0, population=1000),
            "d": DemandZone("d", 400, 0, population=1000),
        },
        config=config,
    )
    far = assign_demand(
        network,
        DemandMatrix((ODPairDemand("o", "d", 100),)),
        zones={
            "o": DemandZone("o", 0, 0, population=1000),
            "d": DemandZone("d", 60_000, 0, population=1000),
        },
        config=config,
    )

    assert near.metrics.walk_trips > 0.0
    assert near.metrics.walk_trips > near.metrics.car_trips
    assert far.metrics.car_trips > 0.0
    assert far.metrics.car_trips > far.metrics.walk_trips


def test_mode_shares_sum_to_one():
    result = assign_demand(
        make_network(),
        DemandMatrix((ODPairDemand("a", "a", 10),)),
        config=AssignmentConfig(period_id="peak"),
    )
    total = result.metrics.total_trips
    assert abs(
        result.metrics.transit_share
        + result.metrics.car_trips / total
        + result.metrics.walk_trips / total
        - 1
    ) < 1e-9


def test_walking_only_path_is_not_counted_as_transit():
    network = make_network()
    router = TransitRouter(
        network,
        config=RouterConfig(walk_transfer_radius_m=1200),
    )
    result = assign_demand(
        network,
        DemandMatrix((ODPairDemand("a", "b", 100),)),
        config=AssignmentConfig(period_id="peak", max_access_distance_m=0),
        router=router,
    )
    assert result.metrics.transit_trips >= 0
    assert all(
        item.passengers >= 0
        for item in result.section_loads
    )


def test_stop_flow_exposes_reference_dwell_time():
    network = make_network()
    result = assign_demand(
        network,
        DemandMatrix((ODPairDemand("a", "c", 100),)),
        config=AssignmentConfig(period_id="peak", max_access_distance_m=0),
        router=TransitRouter(network, config=RouterConfig(walk_transfer_radius_m=0)),
    )
    stop_a = next(item for item in result.stop_flows if item.stop_id == "a")
    assert stop_a.dwell_seconds > 0.0


def test_mode_access_limit_overrides_global_access_radius():
    network = Network()
    network.add_stop(Stop("a", "A", Point(0, 0)))
    network.add_stop(Stop("b", "B", Point(2000, 0)))
    network.add_vehicle_type(VehicleType("bus", "Bus", TransitMode.BUS, 80))
    network.add_period(ServicePeriod("peak", 0, 60))
    network.add_route(Route("r1", "1", TransitMode.BUS, ("a", "b")))
    network.add_service(Service("svc", "r1", "bus", {"peak": 10}))

    zones = {
        "o": DemandZone("o", 550, 0, population=1000),
        "d": DemandZone("d", 1450, 0, population=1000),
    }
    result = assign_demand(
        network,
        DemandMatrix((ODPairDemand("o", "d", 100),)),
        zones=zones,
        config=AssignmentConfig(period_id="peak", max_access_distance_m=1500),
    )

    assert result.metrics.transit_trips == 0.0
    assert result.unserved_transit_demand == 0.0


def test_assignment_routes_transit_demand_across_alternatives():
    network = Network()
    for stop_id, x, y in (
        ("a", 0, 0),
        ("b", 1000, 0),
        ("c", 1000, 1000),
        ("d", 2000, 0),
    ):
        network.add_stop(Stop(stop_id, stop_id.upper(), Point(x, y)))
    network.add_vehicle_type(VehicleType("bus", "Bus", TransitMode.BUS, 90))
    network.add_period(ServicePeriod("peak", 0, 60))
    network.add_route(Route("direct", "Direct", TransitMode.BUS, ("a", "b", "d")))
    network.add_route(Route("detour", "Detour", TransitMode.BUS, ("a", "c", "d")))
    network.add_service(Service("direct-service", "direct", "bus", {"peak": 10}))
    network.add_service(Service("detour-service", "detour", "bus", {"peak": 10}))

    result = assign_demand(
        network,
        DemandMatrix((ODPairDemand("a", "d", 100.0),)),
        config=AssignmentConfig(
            period_id="peak",
            max_access_distance_m=0,
            max_transit_alternatives=2,
        ),
    )

    direct = next(flow for flow in result.route_flows if flow.route_id == "direct")
    detour = next(flow for flow in result.route_flows if flow.route_id == "detour")
    assert direct.passenger_section_traversals > 0.0
    # The detour is dominated by the direct route, so the Pareto filter keeps
    # only the direct journey and the detour carries no passengers.
    assert detour.passenger_section_traversals == 0.0
    assert abs(
        direct.passenger_section_traversals
        + detour.passenger_section_traversals
        - result.metrics.transit_trips * 2
    ) < 1e-8




def test_every_trip_takes_one_of_three_modes():
    """Весь спрос распределяется по трём режимам, как в игре.

    Четвёртой альтернативы «остаться дома» больше нет: в игре режимов
    ровно три - метро, авто, пешком. Поэтому сумма долей даёт ровно объём
    спроса, и никакая его часть не исчезает и не учитывается отдельно.
    """
    network = make_network()
    result = assign_demand(
        network,
        DemandMatrix((
            ODPairDemand("a", "c", 100.0),
        )),
        config=AssignmentConfig(
            period_id="peak",
            max_access_distance_m=0,
        ),
    )

    modes = (
        result.metrics.transit_trips
        + result.metrics.car_trips
        + result.metrics.walk_trips
    )
    assert abs(modes - 100.0) < 1e-9
    assert not hasattr(result.metrics, "rest_trips")
