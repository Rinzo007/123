"""Правила пути и восприятия времени по разбору Subway Builder 1.6.0."""
from __future__ import annotations

import math

import pytest

from transit_planner.game_rules import (
    ARRIVAL_GAP_S,
    CONGESTION_FULL_AT_MIN,
    DRIVING_TIME_MULTIPLIERS,
    MAX_TRANSFERS,
    MAX_WALK_TO_FROM_STATION_S,
    MIN_GAP_MINUTES,
    MIN_TRANSIT_CHOICE,
    PerceivedMinutes,
    airport_parking_cost,
    driving_seconds,
    driving_time_multiplier,
    home_work_departure_pair,
    income_for_person,
    inverse_normal_cdf,
    parking_search_minutes,
    perceived_minutes,
    short_drive_penalty,
    value_of_time_s_per_eur,
    walking_seconds,
    wait_seconds,
)


def _reference_inverse_cdf(probability: float) -> float:
    """Независимая проверка: бисекция по точной CDF."""
    low, high = -9.0, 9.0
    for _ in range(200):
        middle = (low + high) / 2
        if 0.5 * (1.0 + math.erf(middle / math.sqrt(2.0))) < probability:
            low = middle
        else:
            high = middle
    return (low + high) / 2


@pytest.mark.parametrize(
    ("probability", "expected"),
    [
        (0.001, -3.090232),
        (0.025, -1.959964),
        (0.1, -1.281552),
        (0.5, 0.0),
        (0.9, 1.281552),
        (0.975, 1.959964),
        (0.999, 3.090232),
    ],
)
def test_inverse_normal_cdf_matches_table(probability: float, expected: float) -> None:
    assert inverse_normal_cdf(probability) == pytest.approx(expected, abs=1e-6)


def test_inverse_normal_cdf_matches_exact_cdf_everywhere() -> None:
    worst = max(
        abs(inverse_normal_cdf(i / 1000) - _reference_inverse_cdf(i / 1000))
        for i in range(1, 1000)
    )
    assert worst < 1e-10


def test_inverse_normal_cdf_is_monotone() -> None:
    values = [inverse_normal_cdf(i / 100_000) for i in range(1, 100_000)]
    assert all(later > earlier for earlier, later in zip(values, values[1:]))


def test_inverse_normal_cdf_is_symmetric() -> None:
    assert inverse_normal_cdf(0.05) + inverse_normal_cdf(0.95) == pytest.approx(0.0, abs=1e-12)


def test_inverse_normal_cdf_clamps_beyond_domain() -> None:
    assert math.isfinite(inverse_normal_cdf(0.0))
    assert math.isfinite(inverse_normal_cdf(1.0))


def test_income_grows_with_index_and_stays_in_bounds() -> None:
    incomes = [income_for_person(i, total=100) for i in range(100)]
    assert incomes[0] == 15_000.0
    assert incomes[-1] > incomes[len(incomes) // 2]
    assert all(15_000.0 <= value <= 200_000.0 for value in incomes)


def test_income_is_deterministic() -> None:
    assert income_for_person(7, total=100) == income_for_person(7, total=100)


def test_airport_and_college_income_shift() -> None:
    base = income_for_person(50, total=100)
    assert income_for_person(50, total=100, to_airport=True) == pytest.approx(base * 1.5)
    assert income_for_person(50, total=100, at_college=True) == pytest.approx(base * 0.6)


def test_value_of_time_is_income_over_hours() -> None:
    assert value_of_time_s_per_eur(60_000.0) == pytest.approx(60_000.0 / 1860.0)
    assert value_of_time_s_per_eur(-5.0) == 0.0


def test_driving_time_scales_with_congestion() -> None:
    free_flow = driving_seconds(10_000.0, demand_level="veryLow")
    peak = driving_seconds(10_000.0, demand_level="high")
    assert peak == pytest.approx(free_flow * 1.5 / 0.8)
    assert driving_time_multiplier("unknown-level") == 1.0
    assert set(DRIVING_TIME_MULTIPLIERS) == {
        "veryLow", "low", "lowMedium", "medium", "high",
    }


def test_driving_time_has_floor() -> None:
    assert driving_seconds(0.0) == 60.0


def test_short_drive_penalty_decays_to_one() -> None:
    assert short_drive_penalty(500.0) == pytest.approx(1.5)
    assert short_drive_penalty(1_000.0) == pytest.approx(1.0)
    assert short_drive_penalty(5_000.0) == 1.0


def test_waiting_uses_wardman_multiplier() -> None:
    assert wait_seconds(600.0) == pytest.approx(600.0 * 1.37)


def test_parking_search_uses_wardman_multiplier() -> None:
    assert parking_search_minutes(180.0) == pytest.approx(3.0 * 1.6)


def test_walk_weighted_to_airport_is_harder() -> None:
    assert walking_seconds(1_000.0, to_airport=True) > walking_seconds(1_000.0)


def test_departure_pair_keeps_minimum_gap() -> None:
    for index in range(50):
        home, work = home_work_departure_pair(index, seed=3)
        gap = (work - home) % 86_400.0
        assert gap == pytest.approx(MIN_GAP_MINUTES * 60.0)
        assert 0.0 <= home < 86_400.0
        assert 0.0 <= work < 86_400.0


def test_airport_parking_cost_is_multiplied() -> None:
    assert airport_parking_cost() == pytest.approx(25.0)


def test_perceived_minutes_prefers_car_on_short_commute() -> None:
    result = perceived_minutes(
        in_vehicle_s=1_500.0, wait_s=300.0, access_walk_s=400.0,
        egress_walk_s=400.0, transfer_walk_s=0.0, transfers=1,
        car_drive_s=1_200.0, car_parking_s=180.0, car_distance_m=12_000.0,
        walk_s=3_000.0,
    )
    assert result.winner() == "driving"
    assert result.transit > result.car
    assert result.car < result.walk


def test_perceived_minutes_falls_back_when_transit_is_thin() -> None:
    alone = PerceivedMinutes(transit=10.0, car=20.0, walk=30.0)
    assert alone.preferred(transit_pax=MIN_TRANSIT_CHOICE - 1) == "driving"
    assert alone.preferred(transit_pax=MIN_TRANSIT_CHOICE) == "transit"


def test_arrival_gap_applies_to_every_transfer() -> None:
    one = perceived_minutes(
        in_vehicle_s=600.0, wait_s=0.0, access_walk_s=0.0, egress_walk_s=0.0,
        transfer_walk_s=0.0, transfers=1, car_drive_s=600.0, car_parking_s=0.0,
        car_distance_m=20_000.0, walk_s=6_000.0,
    )
    two = perceived_minutes(
        in_vehicle_s=600.0, wait_s=0.0, access_walk_s=0.0, egress_walk_s=0.0,
        transfer_walk_s=0.0, transfers=2, car_drive_s=600.0, car_parking_s=0.0,
        car_distance_m=20_000.0, walk_s=6_000.0,
    )
    assert two.transit - one.transit == pytest.approx(ARRIVAL_GAP_S * 1.37 / 60.0)


def test_path_rules_match_parsed_values() -> None:
    assert MAX_TRANSFERS == 4
    assert MAX_WALK_TO_FROM_STATION_S == 45 * 60
    assert CONGESTION_FULL_AT_MIN == 2.0
