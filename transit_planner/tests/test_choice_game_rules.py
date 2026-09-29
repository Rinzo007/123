"""Подключение правил из разбора к основному пути mode choice."""
from __future__ import annotations

import pytest

from transit_planner.choice import ChoiceConfig, utilities
from transit_planner.game_rules import value_of_time_s_per_eur


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
    # С/евро: бедному минута дороже, поэтому VOT у него больше.
    assert poor.value_of_time_s_per_eur > rich.value_of_time_s_per_eur
    # Богатый пассажир платит за минуту дороже в евро, поэтому обобщённая
    # стоимость у него выше, а полезность ниже.
    def utility(config: ChoiceConfig) -> float:
        return utilities(
            walk_time_min=30.0, car_time_min=20.0, transit_time_min=None,
            car_distance_km=15.0, config=config,
        ).car
    assert utility(rich) < utility(poor)


def test_legacy_vot_sits_inside_the_game_income_range() -> None:
    """Устаревшая константа 360 с/евро не выпадает из игрового диапазона.

    VOT = 1860*3600 / доход, поэтому 360 с/евро соответствует доходу
    18 600 EUR в год - это низкая часть диапазона MINIMUM_INCOME=15 000 ...
    MAXIMUM_INCOME=200 000, а не ошибка на порядок. Ранее здесь стояло
    утверждение об обратном, порождённое ошибкой размерности в VOT.
    """
    base = ChoiceConfig()
    legacy = base.value_of_time_s_per_eur
    assert value_of_time_s_per_eur(15_000.0) > legacy > value_of_time_s_per_eur(200_000.0)
    assert 15_000.0 < 1860.0 * 3600.0 / legacy < 200_000.0


def test_income_zero_keeps_config_usable() -> None:
    assert ChoiceConfig().for_income(0.0).value_of_time_s_per_eur > 0.0


def test_defaults_come_from_parsed_game_values() -> None:
    config = ChoiceConfig()
    assert config.min_sensible_driving_m == 1000.0
    assert config.arrival_gap_minutes == pytest.approx(50.0 / 60.0)
