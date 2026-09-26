from transit_planner.assignment import AssignmentConfig, assign_demand
from transit_planner.demand import DemandMatrix, ODPairDemand
from transit_planner.economics import (
    EconomicsConfig,
    aggregate_temporal_economics,
    calculate_economics,
)
from transit_planner.infrastructure import TrackSection, TrackType
from transit_planner.geo import Point
from transit_planner.network import (
    Network, Route, Service, ServicePeriod, Stop, TransitMode, VehicleType,
)
from transit_planner.reference_model import TrackRow


def test_operating_economics():
    network = Network()
    network.add_stop(Stop("a", "A", Point(0, 0)))
    network.add_stop(Stop("b", "B", Point(1000, 0)))
    network.add_vehicle_type(VehicleType("bus", "Bus", TransitMode.BUS, 80, 2.0))
    network.add_period(ServicePeriod("peak", 0, 60))
    network.add_route(Route("r1", "1", TransitMode.BUS, ("a", "b")))
    network.add_service(Service("svc", "r1", "bus", {"peak": 10}))

    assignment = assign_demand(
        network,
        DemandMatrix((ODPairDemand("a", "b", 100),)),
        config=AssignmentConfig(period_id="peak", max_access_distance_m=0),
    )
    result = calculate_economics(
        network,
        assignment,
        config=EconomicsConfig(period_id="peak", fare_per_transit_trip=2.0),
    )
    assert result.daily_vehicle_km == 12.0
    assert result.daily_fare_revenue > 0
    assert result.annual_operating_cost == result.daily_operating_cost * 365


def test_reference_fleet_cost_is_calculated():
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

    assert result.daily_fleet_cost == 250.0
    assert result.annual_fleet_cost == 250.0 * 365


def test_linked_track_sections_drive_capital_cost_by_track_type():
    network = Network()
    network.add_stop(Stop("a", "A", Point(0, 0), True))
    network.add_stop(Stop("b", "B", Point(1000, 0), True))
    network.add_track_section(TrackSection("surface", 0.75, TrackType.SURFACE))
    network.add_track_section(TrackSection("tunnel", 1.25, TrackType.TUNNEL))
    network.add_vehicle_type(VehicleType("metro", "Metro", TransitMode.METRO, 750))
    network.add_period(ServicePeriod("peak", 0, 60))
    network.add_route(
        Route(
            "r1",
            "1",
            TransitMode.METRO,
            ("a", "b"),
            track_section_ids=("tunnel",),
        )
    )
    network.add_service(Service("svc", "r1", "metro", {"peak": 10}))

    assignment = assign_demand(
        network,
        DemandMatrix((ODPairDemand("a", "b", 10),)),
        config=AssignmentConfig(period_id="peak", max_access_distance_m=0),
    )
    result = calculate_economics(
        network,
        assignment,
        config=EconomicsConfig(
            period_id="peak",
            infrastructure_cost_per_km={TransitMode.METRO: 1.0},
            infrastructure_cost_per_track_km={
                TrackType.SURFACE: 2.0,
                TrackType.TUNNEL: 10.0,
            },
            station_cost=3.0,
        ),
    )

    assert result.capital_cost == 1.25 * 10.0 + 2 * 3.0


def test_route_capital_cost_is_not_duplicated_across_services() -> None:
    network = Network()
    network.add_stop(Stop("a", "A", Point(0, 0)))
    network.add_stop(Stop("b", "B", Point(1000, 0)))
    network.add_track_section(TrackSection("s1", 1.0, TrackType.SURFACE))
    network.add_vehicle_type(VehicleType("tram", "Tram", TransitMode.TRAM, 250))
    network.add_period(ServicePeriod("am", 360, 540))
    network.add_route(
        Route(
            "r1",
            "1",
            TransitMode.TRAM,
            ("a", "b"),
            track_section_ids=("s1",),
        )
    )
    network.add_service(Service("svc-am-1", "r1", "tram", {"am": 10}))
    network.add_service(Service("svc-am-2", "r1", "tram", {"am": 15}))

    assignment = assign_demand(
        network,
        DemandMatrix((ODPairDemand("a", "b", 1.0),)),
        config=AssignmentConfig(period_id="am", max_access_distance_m=0),
    )
    result = calculate_economics(
        network,
        assignment,
        config=EconomicsConfig(
            period_id="am",
            infrastructure_cost_per_track_km={TrackType.SURFACE: 7.0},
        ),
    )

    assert result.capital_cost == 7.0


def test_physical_station_links_prevent_duplicate_station_capex() -> None:
    network = Network()
    network.add_stop(Stop("a", "A", Point(0, 0), True))
    network.add_stop(Stop("b", "B", Point(1000, 0), True))
    network.add_stop(Stop("c", "C", Point(2000, 0), True))
    network.add_track_section(
        TrackSection("s1", 1.0, station_ids=("a", "b"))
    )
    network.add_track_section(
        TrackSection("s2", 1.0, station_ids=("b", "c"))
    )
    network.add_vehicle_type(VehicleType("tram", "Tram", TransitMode.TRAM, 250))
    network.add_period(ServicePeriod("am", 360, 540))
    network.add_route(
        Route(
            "r1",
            "1",
            TransitMode.TRAM,
            ("a", "b", "c"),
            track_section_ids=("s1", "s2"),
            closed=False,
        )
    )
    network.add_service(Service("svc", "r1", "tram", {"am": 10}))

    assignment = assign_demand(
        network,
        DemandMatrix((ODPairDemand("a", "b", 1.0),)),
        config=AssignmentConfig(period_id="am", max_access_distance_m=0),
    )
    result = calculate_economics(
        network,
        assignment,
        config=EconomicsConfig(
            period_id="am",
            station_cost=3.0,
        ),
    )

    assert result.capital_cost == 18.0 + 3 * 3.0


def test_reference_row_costs_drive_default_capital_cost():
    network = Network()
    network.add_stop(Stop("a", "A", Point(0, 0)))
    network.add_stop(Stop("b", "B", Point(1000, 0)))
    network.add_vehicle_type(VehicleType("bus", "Bus", TransitMode.BUS, 90))
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

    assert result.capital_cost == 0.4


def test_segment_reference_row_overrides_default_mode_row_for_capital_cost():
    network = Network()
    network.add_stop(Stop("a", "A", Point(0, 0)))
    network.add_stop(Stop("b", "B", Point(1000, 0)))
    network.add_stop(Stop("c", "C", Point(2000, 0)))
    network.add_vehicle_type(VehicleType("tram", "Tram", TransitMode.TRAM, 250))
    network.add_period(ServicePeriod("peak", 0, 60))
    network.add_route(
        Route(
            "r1",
            "1",
            TransitMode.TRAM,
            ("a", "b", "c"),
            row_by_segment=(TrackRow.RESERVED, TrackRow.GRADE),
        )
    )
    network.add_service(Service("svc", "r1", "tram", {"peak": 10}))

    assignment = assign_demand(
        network,
        DemandMatrix((ODPairDemand("a", "c", 10),)),
        config=AssignmentConfig(period_id="peak", max_access_distance_m=0),
    )
    result = calculate_economics(
        network,
        assignment,
        config=EconomicsConfig(period_id="peak"),
    )

    assert result.capital_cost == 18.0 + 85.0


def test_reference_capital_cost_multipliers_apply_to_rows():
    network = Network()
    network.add_stop(Stop("a", "A", Point(0, 0)))
    network.add_stop(Stop("b", "B", Point(1000, 0)))
    network.add_vehicle_type(VehicleType("tram", "Tram", TransitMode.TRAM, 250))
    network.add_period(ServicePeriod("peak", 0, 60))
    network.add_route(
        Route(
            "r1",
            "1",
            TransitMode.TRAM,
            ("a", "b"),
            row_by_segment=(TrackRow.RESERVED,),
        )
    )
    network.add_service(Service("svc", "r1", "tram", {"peak": 10}))
    assignment = assign_demand(
        network,
        DemandMatrix((ODPairDemand("a", "b", 1),)),
        config=AssignmentConfig(period_id="peak", max_access_distance_m=0),
    )
    result = calculate_economics(
        network,
        assignment,
        config=EconomicsConfig(
            period_id="peak",
            reference_cost_multiplier=2.0,
            reference_row_cost_multipliers={TrackRow.RESERVED: 1.5},
        ),
    )
    assert result.capital_cost == 18.0 * 2.0 * 1.5


def test_temporal_economics_sums_operations_and_counts_capital_once():
    network = Network()
    network.add_stop(Stop("a", "A", Point(0, 0)))
    network.add_stop(Stop("b", "B", Point(1000, 0)))
    network.add_vehicle_type(VehicleType("bus", "Bus", TransitMode.BUS, 80, 2.0))
    network.add_period(ServicePeriod("am", 360, 540))
    network.add_period(ServicePeriod("pm", 900, 1080))
    network.add_route(Route("r1", "1", TransitMode.BUS, ("a", "b")))
    network.add_service(
        Service("svc", "r1", "bus", {"am": 10, "pm": 10})
    )

    demand = DemandMatrix((ODPairDemand("a", "b", 100),))
    am_assignment = assign_demand(
        network,
        demand,
        config=AssignmentConfig(period_id="am", max_access_distance_m=0),
    )
    pm_assignment = assign_demand(
        network,
        demand,
        config=AssignmentConfig(period_id="pm", max_access_distance_m=0),
    )

    from transit_planner.temporal_assignment import PeriodAssignment, TemporalAssignmentResult

    temporal = TemporalAssignmentResult((
        PeriodAssignment("am", demand.total_trips_per_day, am_assignment),
        PeriodAssignment("pm", demand.total_trips_per_day, pm_assignment),
    ))
    result = aggregate_temporal_economics(
        network,
        temporal,
        config=EconomicsConfig(period_id="am"),
    )

    assert result.daily_vehicle_km == 72.0
    assert result.daily_operating_cost == 144.0
    assert result.capital_cost == 0.4
    assert result.daily_fleet_cost == 250.0
