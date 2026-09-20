"""Mode choice: transit / car / walk / eBike shares by Takt hierarchy.

Две модели:
- ``_takt_mode_shares`` — иерархия движка Takt (``po``, 085f71988f12f8f584de.js):
  полезности ``exp(-cost_s / ks)``, ``ks`` — VOT с/€ (Pa = 360); доля зоны
  без авто ``be`` (Z[qt]) смешивает l- и r-варианты;
- ``_mode_minutes``/``_logit_probs``/``_car_cap_prob`` — прежний простой
  логит по обобщённым минутам (резерв на время миграции).

Общие стоимости режимов считаются в секундах:
car = время движения + parking + (км×€/км + €/парк)×VOT;
walk = дистанция×circuity / скорость; eBike — c жёстким порогом reach.
"""
from __future__ import annotations

import math

import numpy as np

from ..base.models import ModeChoiceConfig


def _mode_minutes(
    mode: ModeChoiceConfig,
    od_meters: float,
    transit_min: float | None,
    fare_add_min: float,
) -> tuple[list[float], int, int, int, int]:
    """Стоимости режимов в минутах и индексы (transit/car/walk/ebike).

    Возвращает (минуты, индекс transit, индекс car, индекс walk,
    индекс ebike). ``transit`` включается, только если найден маршрут
    (``transit_min`` не None); ``car`` и ``walk`` присутствуют всегда;
    ``ebike`` активен, только если ``two_wheel_share > 0`` и поездка не
    длиннее ``two_wheel_reach_m`` (индекс −1 в противном случае).
    """
    minutes: list[float] = []
    transit_index = -1
    od_km = od_meters / 1000.0

    if transit_min is not None:
        transit_index = len(minutes)
        minutes.append(transit_min + fare_add_min)

    car_index = len(minutes)
    car_cost_s = (
        od_km * mode.car_circuity / (mode.car_speed_kmh / 3.6)
        + mode.car_parking_min * 60.0
        + (
            od_km * mode.car_circuity * mode.car_cost_per_km_eur
            + mode.car_parking_eur
        )
        * mode.vot_per_eur_s
    )
    minutes.append(car_cost_s / 60.0)

    walk_index = len(minutes)
    walk_min = od_meters * mode.walk_circuity / (mode.walk_speed_mps * 60.0)
    minutes.append(walk_min)

    ebike_index = -1
    if (
        mode.two_wheel_share > 0.0
        and od_meters <= mode.two_wheel_reach_m
    ):
        ebike_index = len(minutes)
        extra_km = max(0.0, od_meters - 6000.0)
        ebike_s = (
            mode.two_wheel_fixed_s
            + (od_meters + extra_km)
            / mode.two_wheel_speed_mps
            * mode.two_wheel_circuity
            + od_km * mode.two_wheel_per_km_eur * mode.vot_per_eur_s
        )
        minutes.append(ebike_s / 60.0)

    return minutes, transit_index, car_index, walk_index, ebike_index

def _car_cap_prob(
    probs: np.ndarray,
    car_index: int,
    no_car_share: float,
) -> None:
    """Ограничивает долю авто долей населения без машины, перераспределяя
    избыток на остальные режимы пропорционально."""
    cap = max(0.0, 1.0 - no_car_share)
    if cap >= probs[car_index]:
        return
    excess = probs[car_index] - cap
    probs[car_index] = cap
    others = [i for i in range(len(probs)) if i != car_index]
    total = float(sum(probs[i] for i in others))
    if total <= 0.0:
        return
    for i in others:
        probs[i] += excess * (probs[i] / total)

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

def _od_fare_min(
    mode: ModeChoiceConfig,
    od_meters: float,
    transit_min: float | None,
) -> float:
    """Тариф поездки, пересчитанный в минуты через VOT."""
    return _od_fare_eur(mode, od_meters, transit_min) * mode.vot_per_eur_s / 60.0

def _logit_probs(costs: np.ndarray, logit_temp: float) -> np.ndarray:
    """Мягкий max по стоимостям; при logit_temp <= 0 — жёсткий выбор."""
    if logit_temp > 0:
        shifted = -(costs / logit_temp)
        shifted = shifted - shifted.max()
        exp = np.exp(shifted)
        total = exp.sum()
        if total <= 0:
            return np.zeros_like(exp)
        return exp / total
    best = np.zeros_like(costs)
    best[int(np.argmin(costs))] = 1.0
    return best


def _takt_car_cost_s(mode: ModeChoiceConfig, od_meters: float) -> float:
    """Стоимость авто в секундах обобщённого времени (Takt ``Oe``).

    = время движения (км×circuity / speed) + parking_s +
      (км×circuity×costPerKm + parkEur)×VOT.
    """
    od_km = od_meters / 1000.0
    return (
        od_km * mode.car_circuity / (mode.car_speed_kmh / 3.6)
        + mode.car_parking_min * 60.0
        + (od_km * mode.car_circuity * mode.car_cost_per_km_eur + mode.car_parking_eur)
        * mode.vot_per_eur_s
    )


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
) -> tuple[float, float, float, float]:
    """Доли (transit, car, walk, ebike) по иерархии Takt ``po()``.

    ``be`` — доля населения без авто (``car_no_car_share``): (1−be) выбирает
    из полного набора ``Us = wo+Jt+js+Te``, be — из набора без авто
    ``Hr = Us−Jt``. Полезности ``exp(-cost_s / ks)``, где ``ks`` = VOT (с/€).
    ``fare_eur`` добавляется к стоимости транзита в секундах (fare×VOT).
    ``transit_s=None`` — транзит недоступен (нет маршрута).
    """
    ks = mode.vot_per_eur_s
    be = mode.car_no_car_share

    wo = math.exp(-(transit_s + fare_eur * ks) / ks) if transit_s is not None else 0.0
    jt = math.exp(-_takt_car_cost_s(mode, od_meters) / ks)
    js = math.exp(-_takt_walk_cost_s(mode, od_meters) / ks)
    te = (
        mode.two_wheel_share * math.exp(-_takt_bike_cost_s(mode, od_meters) / ks)
        if mode.two_wheel_share > 0.0
        else 0.0
    )

    us = wo + jt + js + te
    hr = wo + js + te
    if us <= 0.0:
        if transit_s is not None:
            return 1.0, 0.0, 0.0, 0.0
        if js + te > 0.0:
            walk = js / (js + te)
            return 0.0, 0.0, walk, 1.0 - walk
        return 0.0, 1.0, 0.0, 0.0
    if hr <= 0.0:
        transit = wo / us
        car = jt / us
        walk = js / us
        return transit, car, walk, te / us

    transit = (1.0 - be) * (wo / us) + be * (wo / hr)
    car = (1.0 - be) * (jt / us)
    walk = (1.0 - be) * (js / us) + be * (js / hr)
    ebike = (1.0 - be) * (te / us) + be * (te / hr)
    return transit, car, walk, ebike


def _takt_route_probs(costs_s: np.ndarray) -> np.ndarray:
    """Частотный сплит маршрутов по Takt ``Ge = 1/max(1, cost)``.

    Активный набор строится жадным добавлением по возрастанию стоимости,
    пока маргинальная ожидаемая стоимость ``te`` улучшается (``$e``).
    """
    order = np.argsort(costs_s, kind="stable")
    n = len(costs_s)
    if n == 0:
        return np.zeros(0)
    lt = 0.0
    ee = 0.0
    best = math.inf
    selected: list[int] = []
    for pos in order:
        cost = float(costs_s[pos])
        g = 1.0 / max(1.0, cost)
        te = (1.0 + ee + g * cost) / (lt + g)
        if selected and te >= best:
            break
        selected.append(pos)
        lt += g
        ee += g * cost
        best = te
    probs = np.zeros(n)
    if lt > 0.0:
        for pos in selected:
            probs[pos] = (1.0 / max(1.0, float(costs_s[pos]))) / lt
    elif selected:
        probs[selected[0]] = 1.0
    return probs
