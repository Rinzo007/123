from transit_planner.infrastructure import (
    TrackSection,
    TrackType,
    shared_track_departure_capacities,
    shared_track_departure_capacity,
)


def test_shared_track_capacity_is_the_bottleneck():
    sections = (
        TrackSection("a", 1.0, capacity_departures_per_hour=30, shared_group="g1"),
        TrackSection("b", 1.0, capacity_departures_per_hour=20, shared_group="g1"),
    )
    assert shared_track_departure_capacity(sections) == 20


def test_track_speed_limit_must_be_positive() -> None:
    try:
        TrackSection("slow", 1.0, speed_limit_kph=0.0)
    except ValueError as exc:
        assert "speed limit" in str(exc)
    else:
        raise AssertionError("Expected ValueError")


def test_shared_track_capacity_keeps_independent_groups_separate():
    sections = (
        TrackSection("a", 1.0, capacity_departures_per_hour=30, shared_group="g1"),
        TrackSection("b", 1.0, capacity_departures_per_hour=20, shared_group="g1"),
        TrackSection("c", 1.0, capacity_departures_per_hour=40, shared_group="g2"),
        TrackSection("d", 1.0, capacity_departures_per_hour=35, shared_group="g2"),
    )
    assert shared_track_departure_capacities(sections) == {"g1": 20, "g2": 35}


def test_shared_track_capacity_scalar_rejects_mixed_groups():
    sections = (
        TrackSection("a", 1.0, shared_group="g1"),
        TrackSection("b", 1.0, shared_group="g2"),
    )
    try:
        shared_track_departure_capacity(sections)
    except ValueError as exc:
        assert "one shared_group" in str(exc)
    else:
        raise AssertionError("Expected ValueError")
