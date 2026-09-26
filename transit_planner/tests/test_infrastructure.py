from transit_planner.infrastructure import (
    ConstructionRates,
    ConstructionProject,
    TrackSection,
    TrackType,
    YearPlan,
    estimate_project_cost,
    shared_track_departure_capacity,
    total_reserved_capital,
)


def test_project_cost_varies_by_track_type_and_stations():
    project = ConstructionProject(
        "p1",
        "Metro extension",
        (
            TrackSection("s1", 2.0, TrackType.TUNNEL),
            TrackSection("s2", 1.0, TrackType.SURFACE),
        ),
        station_count=3,
    )
    rates = ConstructionRates(
        {
            TrackType.SURFACE: 1.0,
            TrackType.ELEVATED: 2.0,
            TrackType.TUNNEL: 5.0,
        },
        station_cost=0.5,
    )

    cost = estimate_project_cost(project, rates=rates)

    assert cost.construction_cost == 11.0
    assert cost.station_cost == 1.5
    assert cost.total_cost == 12.5


def test_parallel_track_has_independent_multiplier():
    project = ConstructionProject(
        "p2",
        "Parallel",
        (TrackSection("s1", 4.0),),
        parallel=True,
    )
    rates = ConstructionRates(
        {TrackType.SURFACE: 2.0},
        parallel_track_multiplier=1.25,
    )

    assert estimate_project_cost(project, rates=rates).total_cost == 10.0


def test_shared_track_capacity_is_the_bottleneck():
    sections = (
        TrackSection("a", 1.0, capacity_departures_per_hour=30, shared_group="g1"),
        TrackSection("b", 1.0, capacity_departures_per_hour=20, shared_group="g1"),
    )
    assert shared_track_departure_capacity(sections) == 20


def test_year_plan_tracks_reserved_capital_and_removals():
    project = ConstructionProject(
        "p1",
        "Surface extension",
        (TrackSection("s1", 3.0),),
    )
    rates = ConstructionRates({TrackType.SURFACE: 2.0})
    plan = YearPlan(2026)
    plan.add_project(project)
    plan.reserve_removal("old-line")

    assert total_reserved_capital(plan, rates=rates) == 6.0
    assert plan.removals == ["old-line"]
