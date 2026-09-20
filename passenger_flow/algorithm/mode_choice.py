"""Единая модель выбора режима и маршрута Takt.

Используется одна иерархическая модель полезностей Takt: транзит, авто,
пешком, eBike и альтернативный ``rest/baseT``. Стоимости приводятся к
секундам обобщённого времени через VOT; доля без автомобиля задаёт
иерархическое смешивание полной и no-car совокупностей.
"""
from __future__ import annotations

import math

import numpy as np

from ..base.models import ModeChoiceConfig
from ..base.defaults import TAKT_CAR_PERIOD_MT, TAKT_CAR_PERIOD_NT

_TAKT_DEBUG_MODE_CALLS: list[dict[str, float | None]] = []


def _takt_no_car_shares(
    mode: ModeChoiceConfig,
    population: np.ndarray | None = None,
) -> np.ndarray | None:
    """Возвращает эффективную долю населения без авто по Takt.

    Базовая доля: noCar × Aa. При наличии населения по зонам применяется
    та же поправка по логарифму плотности и последующая нормализация среднего,
    что в движке Takt. Без массива населения возвращается None: вызывающая
    функция использует общую базовую долю.
    """
    q = min(0.95, max(0.03, mode.car_no_car_share * mode.car_no_car_factor))
    if population is None:
        return None
    pop = np.asarray(population, dtype=np.float64)
    if pop.ndim != 1:
        raise ValueError("population должен быть одномерным массивом")
    positive = pop[pop > 0.0]
    if positive.size == 0:
        return np.full(pop.shape, q, dtype=np.float64)
    ordered = np.sort(positive)
    median_like = float(ordered[ordered.size >> 1])
    floor_pop = np.maximum(1.0, pop)
    density_log10 = np.log(floor_pop / max(1.0, median_like)) / math.log(10.0)
    shares = np.clip(q * (1.0 + 0.45 * density_log10), 0.03, 0.95)
    mean_share = float(np.sum(pop * shares) / max(float(np.sum(pop)), 1e-12))
    scale = q / mean_share if mean_share > 0.0 else 1.0
    shares = np.clip(shares * scale, 0.03, 0.95)
    return shares.astype(np.float64, copy=False)


def _od_fare_eur(
    mode: ModeChoiceConfig,
    od_meters: float,
    transit_min: float | None,
) -> float:
    """Тариф поездки в EUR (без VOT), 0 при нулевом или неактивном тарифе.

    Тариф считается ``max(base, min_fare, base + per_km×км)``; при
    ``fare_cap_eur > 0`` результат ограничивается сверху.
    """
    if mode.fare_base_eur < 0 or transit_min is None:
        return 0.0
    if (
        mode.fare_base_eur == 0.0
        and mode.fare_per_km_eur == 0.0
        and mode.min_fare_eur == 0.0
    ):
        return 0.0
    od_km = od_meters / 1000.0
    rawe = mode.fare_base_eur + mode.fare_per_km_eur * od_km
    lower = max(mode.fare_base_eur, mode.min_fare_eur)
    if mode.fare_cap_eur > 0:
        fare = min(max(rawe, lower), mode.fare_cap_eur)
    else:
        fare = max(rawe, lower)
    return max(fare, 0.0)

def _takt_car_period_multiplier(
    period_demand_per_hour: float,
    average_demand_per_hour: float,
) -> float:
    """Периодный множитель времени авто из Takt yt.

    yt = min(1.8, 1 + 0.6 * max(0, N/T - 1)).
    При отсутствующем/нулевом среднем спросе возвращается 1.0.
    """
    avg = float(average_demand_per_hour)
    demand = float(period_demand_per_hour)
    if not math.isfinite(avg) or avg <= 0.0 or not math.isfinite(demand):
        return 1.0
    return min(
        TAKT_CAR_PERIOD_MT,
        1.0 + TAKT_CAR_PERIOD_NT * max(0.0, demand / avg - 1.0),
    )

def _takt_car_cost_s(
    mode: ModeChoiceConfig,
    od_meters: float,
    *,
    road_time_s: float | None = None,
    period_multiplier: float = 1.0,
) -> float:
    """Стоимость авто в секундах обобщённого времени по модели Takt Wr/Oe.

    Время движения берётся из baseT/road_time_s, когда оно задано;
    иначе используется геометрический fallback. Затем применяется yt.
    Парковка и денежная часть остаются без периодного множителя.
    """
    od_km = od_meters / 1000.0
    fallback_time_s = od_km * mode.car_circuity / (mode.car_speed_kmh / 3.6)
    try:
        base_time = float(road_time_s) if road_time_s is not None else fallback_time_s
    except (TypeError, ValueError):
        base_time = fallback_time_s
    if not math.isfinite(base_time) or base_time < 0.0:
        base_time = fallback_time_s
    try:
        multiplier = float(period_multiplier)
    except (TypeError, ValueError):
        multiplier = 1.0
    if not math.isfinite(multiplier) or multiplier <= 0.0:
        multiplier = 1.0
    monetary_s = (
        od_km * mode.car_circuity * mode.car_cost_per_km_eur
        + mode.car_parking_eur
    ) * mode.vot_per_eur_s
    return base_time * multiplier + mode.car_parking_min * 60.0 + monetary_s

def _takt_bike_cost_s(mode: ModeChoiceConfig, od_meters: float) -> float:
    """Стоимость eBike в секундах обобщённого времени (Takt ``Qt``).

    G = Ze×1.25, Mt = max(0, G - reach); ka = fixed_s, ba = circuity.
    """
    grad = od_meters * 1.25
    extra = max(0.0, grad - mode.two_wheel_reach_m)
    return (
        mode.two_wheel_fixed_s
        + (grad + extra) / mode.two_wheel_speed_mps * mode.two_wheel_circuity
        + grad / 1000.0 * mode.two_wheel_per_km_eur * mode.vot_per_eur_s
    )


def _takt_walk_cost_s(mode: ModeChoiceConfig, od_meters: float) -> float:
    """Стоимость пешей поездки в секундах (Takt ``Ze*1.25/ae``)."""
    return od_meters * mode.walk_circuity / mode.walk_speed_mps


def _takt_mode_shares(
    mode: ModeChoiceConfig,
    od_meters: float,
    transit_s: float | None,
    fare_eur: float,
    rest_s: float | None = None,
    no_car_share: float | None = None,
    road_time_s: float | None = None,
    car_period_multiplier: float = 1.0,
) -> tuple[float, float, float, float, float]:
    """Доли (transit, car, walk, ebike, rest) с альтернативой ``baseT``."""
    ks = mode.vot_per_eur_s
    be = (
        min(0.95, max(0.03, mode.car_no_car_share * mode.car_no_car_factor))
        if no_car_share is None
        else min(0.95, max(0.03, float(no_car_share)))
    )

    transit_u = (
        math.exp(-(transit_s + fare_eur * ks + mode.rider_bias_s) / ks)
        if transit_s is not None
        else 0.0
    )
    car_u = math.exp(
        -_takt_car_cost_s(
            mode,
            od_meters,
            road_time_s=road_time_s,
            period_multiplier=car_period_multiplier,
        )
        / ks
    )
    walk_u = math.exp(-_takt_walk_cost_s(mode, od_meters) / ks)
    ebike_u = (
        mode.two_wheel_share
        * math.exp(-_takt_bike_cost_s(mode, od_meters) / ks)
        if mode.two_wheel_share > 0.0
        else 0.0
    )
    rest_u = math.exp(-max(0.0, float(rest_s)) / ks) if rest_s is not None and rest_s > 0 else 0.0

    if len(_TAKT_DEBUG_MODE_CALLS) < 5:
        _TAKT_DEBUG_MODE_CALLS.append({
            "be": float(be),
            "carCostS": float(_takt_car_cost_s(mode, od_meters, road_time_s=road_time_s, period_multiplier=car_period_multiplier)),
            "transitCostS": None if transit_s is None else float(transit_s),
            "fareEur": float(fare_eur),
            "roadTimeS": None if road_time_s is None else float(road_time_s),
            "carMultiplier": float(car_period_multiplier),
        })
    us = transit_u + car_u + walk_u + ebike_u + rest_u
    hr = transit_u + walk_u + ebike_u + rest_u
    if us <= 0.0:
        return 0.0, 1.0, 0.0, 0.0, 0.0
    if hr <= 0.0:
        return (transit_u / us, car_u / us, walk_u / us, ebike_u / us, rest_u / us)

    transit = (1.0 - be) * (transit_u / us) + be * (transit_u / hr)
    car = (1.0 - be) * (car_u / us)
    walk = (1.0 - be) * (walk_u / us) + be * (walk_u / hr)
    ebike = (1.0 - be) * (ebike_u / us) + be * (ebike_u / hr)
    rest = (1.0 - be) * (rest_u / us) + be * (rest_u / hr)
    return transit, car, walk, ebike, rest


def _takt_route_choice(
    costs_s: np.ndarray,
    frequencies_s: np.ndarray | None = None,
) -> tuple[np.ndarray, float]:
    """Takt route-set choice with separate Rr frequency weights.

    JS uses g=1/max(1,Rr) while the candidate route cost remains ds.
    The optional frequencies_s argument exposes that exact separation;
    the legacy single-array form remains unchanged for existing callers/tests.
    """
    costs = np.asarray(costs_s, dtype=np.float64)
    if costs.ndim != 1:
        costs = costs.reshape(-1)
    if costs.size == 0:
        return np.zeros(0, dtype=np.float64), math.inf
    if frequencies_s is None:
        frequencies = costs.copy()
    else:
        frequencies = np.asarray(frequencies_s, dtype=np.float64)
        if frequencies.shape != costs.shape:
            raise ValueError("frequencies_s must have the same shape as costs_s")
    order = np.argsort(costs, kind="stable")
    lt = 0.0
    ee = 0.0
    best = math.inf
    selected: list[int] = []
    for pos in order:
        cost = float(costs[pos])
        frequency = max(1.0, float(frequencies[pos]))
        g = 1.0 / frequency
        te = (1.0 + ee + g * cost) / (lt + g)
        if selected and te >= best:
            break
        selected.append(int(pos))
        lt += g
        ee += g * cost
        best = te
    probs = np.zeros(costs.size, dtype=np.float64)
    if lt > 0.0:
        for pos in selected:
            frequency = max(1.0, float(frequencies[pos]))
            probs[pos] = (1.0 / frequency) / lt
    elif selected:
        probs[selected[0]] = 1.0
    return probs, best
def _takt_route_probs(costs_s: np.ndarray) -> np.ndarray:
    """Частотный сплит Takt; совместимый тонкий интерфейс."""
    return _takt_route_choice(costs_s)[0]

