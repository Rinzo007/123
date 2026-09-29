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
    circular_gap_s,
    congested_share,
    driving_seconds,
    driving_time_multiplier,
    home_work_departure_pair,
    income_for_person,
    inverse_normal_cdf,
    journey_origin,
    mode_costs,
    parking_search_minutes,
    perceived_driving_seconds,
    perceived_minutes,
    perceived_walk_only_seconds,
    short_drive_penalty,
    value_of_time_eur_per_second,
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
    incomes = [income_for_person(i, total=200) for i in range(200)]
    # Первый человек не упирается в отсечку: анти-кластеризация перерисовывает
    # квантиль, поэтому нижние 15 000 недостижимы.
    assert min(incomes) > 15_000.0
    assert incomes[199] >= incomes[100] > incomes[0]
    assert all(15_000.0 <= value <= 200_000.0 for value in incomes)


def test_income_is_deterministic() -> None:
    assert income_for_person(7, total=200) == income_for_person(7, total=200)


def test_income_multiplier_shifts_mean_not_spread() -> None:
    """Множитель применяется к среднему, разброс остаётся прежним."""
    base = income_for_person(120, total=200)
    airport = income_for_person(120, total=200, job_id="AIR_CLT_T1")
    college = income_for_person(120, total=200, job_id="UNI_UNCC")
    assert airport - base == pytest.approx(30_000.0, rel=0.05)
    assert base - college == pytest.approx(24_000.0, rel=0.05)


def test_value_of_time_matches_documented_control_points() -> None:
    """Документ, раздел 5.1: 15 000 -> 446.4, 60 000 -> 111.6, 200 000 -> 33.5 с/евро."""
    assert value_of_time_s_per_eur(15_000.0) == pytest.approx(446.4, rel=1e-3)
    assert value_of_time_s_per_eur(60_000.0) == pytest.approx(111.6, rel=1e-3)
    assert value_of_time_s_per_eur(200_000.0) == pytest.approx(33.48, rel=1e-3)


def test_value_of_time_reciprocal_forms_agree() -> None:
    assert value_of_time_eur_per_second(60_000.0) == pytest.approx(
        1.0 / value_of_time_s_per_eur(60_000.0)
    )
    assert value_of_time_eur_per_second(60_000.0) * 3600.0 == pytest.approx(32.258, rel=1e-3)
    assert value_of_time_s_per_eur(0.0) == 0.0


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


def test_parking_adds_constant_perceived_seconds() -> None:
    """Раздел 5.2: +180*2*1.6 = 576 с к каждой авто-поездке независимо от длины."""
    for drive in (300.0, 600.0, 1800.0):
        assert perceived_driving_seconds(drive) - drive == pytest.approx(576.0)


def test_congested_share_follows_demand_multiplier() -> None:
    assert congested_share(1.0) == 0.0
    assert congested_share(1.5) == pytest.approx(0.5)
    assert congested_share(2.0) == 1.0
    assert congested_share(0.8) == 0.0
    assert congested_share(9.0) == 1.0


def test_peak_congestion_amplifies_drive_time() -> None:
    free = perceived_driving_seconds(600.0, traffic_multiplier=1.0)
    peak = perceived_driving_seconds(600.0, traffic_multiplier=1.5)
    # 600 * 1.5 * (1 + 0.5*0.33) = 1048.5, плюс постоянные 576.
    assert peak - 576.0 == pytest.approx(1048.5)


def test_walk_only_is_unweighted_like_the_game() -> None:
    """Воспроизведено поведение игры: чисто пеший вариант без 1.39.

    В игре (popCommuteWorker:38749) пеший вариант не взвешивается, хотя те
    же пешие отрезки внутри rRAPTOR идут с 1.39. Флага такого в игре нет:
    реестр фич-флагов полный и статический (раздел 13.1 разбора).
    `apply_walk_multiplier=True` оставлен для сверки и даёт 1.39.
    """
    assert perceived_walk_only_seconds(1200.0) == 1200.0
    assert perceived_walk_only_seconds(
        1200.0, apply_walk_multiplier=True,
    ) == pytest.approx(1200.0 * 1.39)
    assert perceived_walk_only_seconds(1200.0, to_airport=True) == pytest.approx(1200.0 * 1.87)


def test_mode_costs_are_in_euros_and_pick_the_minimum() -> None:
    perceived = perceived_minutes(
        in_vehicle_s=1500.0, wait_s=300.0, access_walk_s=400.0,
        egress_walk_s=400.0, transfer_walk_s=0.0, transfers=1,
        car_drive_s=1200.0, car_parking_s=0.0, car_distance_m=12_000.0,
        walk_s=3000.0,
    )
    costs = mode_costs(income_per_year=60_000.0, transit=perceived, car_distance_m=12_000.0)
    # Порядок величин - десятки евро, а не сотни тысяч.
    assert 1.0 < costs.driving < 200.0
    assert 1.0 < costs.transit < 200.0
    assert 1.0 < costs.walking < 200.0
    assert costs.choice() == min(
        ("driving", "transit", "walking"),
        key=lambda key: {"driving": costs.driving, "transit": costs.transit,
                         "walking": costs.walking}[key],
    )


def test_short_trip_penalty_scales_the_whole_car_cost() -> None:
    """Штраф умножает всю авто-стоимость вместе с деньгами, а не только время."""
    def driving_cost(distance: float) -> float:
        perceived = perceived_minutes(
            in_vehicle_s=0.0, wait_s=0.0, access_walk_s=0.0, egress_walk_s=0.0,
            transfer_walk_s=0.0, transfers=0, car_drive_s=300.0,
            car_parking_s=0.0, car_distance_m=distance, walk_s=0.0,
        )
        return mode_costs(
            income_per_year=60_000.0, transit=perceived, car_distance_m=distance,
        ).driving
    short, sensible = driving_cost(400.0), driving_cost(1_200.0)
    assert short > sensible


def test_circular_gap_wraps_around_midnight() -> None:
    assert circular_gap_s(3600.0, 0.0) == pytest.approx(3600.0)
    assert circular_gap_s(0.0, 86_400.0) == pytest.approx(0.0)
    assert circular_gap_s(100.0, 86_300.0) == pytest.approx(200.0)


def test_journey_origin_uses_nearest_departure_time() -> None:
    home, work = 7 * 3600.0, 19 * 3600.0
    assert journey_origin(home, work, 8 * 3600.0) == "home"
    assert journey_origin(home, work, 18.5 * 3600.0) == "work"
    assert journey_origin(home, work, 12 * 3600.0) == "home"
