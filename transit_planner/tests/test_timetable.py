from transit_planner.timetable import (
    connection_wait,
    generate_service_timetable,
    next_departure,
    wait_minutes,
)


def test_generate_service_timetable_respects_period_and_headway():
    timetable = generate_service_timetable(
        "svc",
        {"peak": (360, 420)},
        {"peak": 15},
        offset_minute=0,
    )

    assert timetable.for_period("peak").departures_minute == (
        360.0,
        375.0,
        390.0,
        405.0,
    )


def test_next_departure_and_wait_use_actual_schedule():
    timetable = generate_service_timetable(
        "svc",
        {"peak": (360, 420)},
        {"peak": 15},
        offset_minute=5,
    ).for_period("peak")

    assert next_departure(timetable, 371.0) == 380.0
    assert wait_minutes(timetable, 371.0) == 9.0


def test_connection_wait_targets_first_feasible_departure():
    timetable = generate_service_timetable(
        "svc",
        {"peak": (360, 420)},
        {"peak": 20},
        offset_minute=0,
    ).for_period("peak")

    assert connection_wait(377.5, timetable) == 2.5
