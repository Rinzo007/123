"""Подключение правил из разбора к основному пути mode choice."""
from __future__ import annotations

import pytest

from transit_planner.choice import ChoiceConfig, utilities


def test_short_drive_penalty_applies_only_with_known_distance() -> None:
    """Ноль километров означает «нет данных», а не поездку нулевой длины."""
    with_default = utilities(
        walk_time_min=20.0, car_time_min=10.0, transit_time_min=None,
        car_distance_km=0.0, config=ChoiceConfig(),
    )
    without_rule = utilities(
        walk_time_min=20.0, car_time_min=10.0, transit_time_min=None,
        car_distance_km=0.0, config=ChoiceConfig(min_sensible_driving_m=0.0),
    )
    assert with_default.car == pytest.approx(without_rule.car)


def test_short_drive_penalty_makes_short_trip_unattractive() -> None:
    config = ChoiceConfig()
    short = utilities(
        walk_time_min=20.0, car_time_min=10.0, transit_time_min=None,
        car_distance_km=0.4, config=config,
    )
    sensible = utilities(
        walk_time_min=20.0, car_time_min=10.0, transit_time_min=None,
        car_distance_km=1.2, config=config,
    )
    assert short.car < sensible.car


def test_arrival_gap_adds_one_step_per_transfer() -> None:
    config = ChoiceConfig()
    def car_cost(transfers: int) -> float:
        return utilities(
            walk_time_min=20.0, car_time_min=10.0, transit_time_min=25.0,
            transit_transfers=transfers, car_distance_km=8.0, config=config,
        ).transit
    assert car_cost(0) > car_cost(1) > car_cost(2)


def test_value_of_time_follows_income() -> None:
    base = ChoiceConfig()
    poor = base.for_income(20_000.0)
    rich = base.for_income(120_000.0)
    assert poor.value_of_time_s_per_eur < rich.value_of_time_s_per_eur
    # Богатый пассажир сильнее чувствует время в пути: минута езды обходится
    # ему дороже, поэтому авто для него привлекательнее, а не наоборот.
    def utility(config: ChoiceConfig) -> float:
        return utilities(
            walk_time_min=30.0, car_time_min=20.0, transit_time_min=None,
            car_distance_km=15.0, config=config,
        ).car
    assert utility(rich) > utility(poor)


def test_legacy_vot_is_far_above_game_value() -> None:
    """Явная фиксация расхождения, а не его сокрытие заменой числа.

    Устаревшая константа reference_model (360 сек/евро) в разы выше игрового
    VOT = доход / 1860 часов. Пока модель на неё опирается, денежные
    составляющие занижены, поэтому подключение доходного VOT меняет
    численные результаты mode choice - это ожидаемо.
    """
    base = ChoiceConfig()
    for income in (15_000.0, 60_000.0, 200_000.0):
        # Минимальное отношение - на потолке дохода: 360 / 107.5 = 3.35.
        assert base.for_income(income).value_of_time_s_per_eur < base.value_of_time_s_per_eur / 3.0


def test_income_zero_keeps_config_usable() -> None:
    assert ChoiceConfig().for_income(0.0).value_of_time_s_per_eur > 0.0


def test_defaults_come_from_parsed_game_values() -> None:
    config = ChoiceConfig()
    assert config.min_sensible_driving_m == 1000.0
    assert config.arrival_gap_minutes == pytest.approx(50.0 / 60.0)
