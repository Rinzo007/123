"""Модели данных расчёта пассажиропотока."""

from __future__ import annotations

from dataclasses import dataclass, field

from .defaults import (
    TAKT_CAR_CIRCUITY,
    TAKT_CAR_COST_PER_KM_EUR,
    TAKT_CAR_PARKING_S,
    TAKT_CAR_PARK_EUR,
    TAKT_CAR_SPEED_KMH,
    TAKT_FARE_BASE_EUR,
    TAKT_FARE_CAP_EUR,
    TAKT_FARE_PER_KM_EUR,
    TAKT_NO_CAR_FACTOR,
    TAKT_NO_CAR_SHARE,
    TAKT_RIDER_BIAS_S,
    TAKT_REST_ACCESS_S,
    TAKT_REST_BASE_SPEED_KMH,
    TAKT_REST_CONT_SPEED_KMH,
    TAKT_REST_CIRCUITY,
    TAKT_REST_WAIT_S,
    TAKT_TWO_WHEEL_CIRCUITY,
    TAKT_TWO_WHEEL_FIXED_S,
    TAKT_TWO_WHEEL_PER_KM_EUR,
    TAKT_TWO_WHEEL_REACH_M,
    TAKT_TWO_WHEEL_SHARE,
    TAKT_TWO_WHEEL_SPEED_MPS,
    TAKT_VOT_S_PER_EUR,
    TAKT_WALK_CIRCUITY,
    TAKT_WALK_SPEED_MPS,
)

class PassengerFlowError(RuntimeError):
    """Ошибка расчёта пассажиропотока."""


@dataclass(frozen=True, slots=True)
class RouteStopData:
    """Данные об остановке в контексте одного маршрута."""

    stop_name: str
    lat: float
    lon: float
    position: int
    stop_id: int | None = None


@dataclass(frozen=True, slots=True)
class RouteDirectionFlow:
    """Пассажиропоток по одному направлению маршрута."""

    direction_name: str
    total_passengers: float
    route_stops: tuple[RouteStopData, ...]


@dataclass(frozen=True, slots=True)
class RouteFlowResult:
    """Пассажиропоток одного маршрута."""

    route_id: int
    route_name: str
    route_type: str
    total_passengers: float
    directions: tuple[RouteDirectionFlow, ...]
    stop_flows: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StopFlowResult:
    """Пассажиропоток на одной уникальной остановке."""

    name: str
    lat: float | None
    lon: float | None
    boardings: float
    alightings: float
    total_flow: float
    routes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Period:
    """Период суток для разложения OD-матрицы по направлениям.

    ``out`` — доля поездок направления ``i→j`` (i < j) в этом периоде,
    ``ret`` — доля обратного направления ``j→i``. Суммы ``out`` и ``ret``
    по всем периодам должны быть равны 1.0, чтобы сохранить общий объём.
    """

    key: str
    label: str
    out: float = 1.0
    ret: float = 1.0


@dataclass(frozen=True, slots=True)
class PeriodFlow:
    """Потоки одного периода суток."""

    key: str
    label: str
    total_trips: float
    assigned_trips: float
    car_trips: float
    walk_trips: float
    two_wheel_trips: float = 0.0
    rest_trips: float = 0.0


@dataclass(frozen=True, slots=True)
class Purpose:
    """Цель поездки (генерация OD по тяготению, по мотивам Takt).

    ``trips_per_res`` — поездок в сутки на жителя по данной цели; ``d0_m`` —
    характерное расстояние затухания тяготения (в метрах); ``k`` — число
    дистанционных направлений: ``max(1, round(k / 4))`` лучших аттракторов
    на каждый из 4 диапазонов движка Takt (доля поездок на дистанцию ``d``
    пропорциональна ``exp(-d / d0)``). ``out``/``ret`` — профили 5 периодов
    суток.
    """

    key: str
    label: str
    trips_per_res: float
    d0_m: float
    k: int = 6
    out: tuple[float, ...] = (1.0,)
    ret: tuple[float, ...] = (1.0,)


_PURPOSE_VALUES: list[tuple[str, str, float, float, int, tuple[float, ...], tuple[float, ...]]] = [
    (
        "edu",
        "School or campus",
        0.16,
        1600.0,
        6,
        (0.04, 0.76, 0.14, 0.05, 0.01),
        (0.0, 0.02, 0.6, 0.32, 0.06),
    ),
    (
        "health",
        "Hospital or clinic",
        0.06,
        3000.0,
        6,
        (0.08, 0.34, 0.36, 0.16, 0.06),
        (0.04, 0.12, 0.36, 0.32, 0.16),
    ),
    (
        "shop",
        "Shops",
        0.34,
        2000.0,
        6,
        (0.01, 0.07, 0.44, 0.36, 0.12),
        (0.01, 0.03, 0.36, 0.42, 0.18),
    ),
    (
        "air",
        "Airport",
        0.03,
        14000.0,
        2,
        (0.18, 0.24, 0.26, 0.2, 0.12),
        (0.06, 0.14, 0.26, 0.28, 0.26),
    ),
    (
        "night",
        "Bar, café or venue",
        0.22,
        3000.0,
        6,
        (0.0, 0.02, 0.14, 0.32, 0.52),
        (0.02, 0.02, 0.08, 0.24, 0.64),
    ),
]


def _purpose_defaults() -> tuple[Purpose, ...]:
    return tuple(
        Purpose(key=k, label=c, trips_per_res=t, d0_m=d, k=kk, out=o, ret=r)
        for k, c, t, d, kk, o, r in _PURPOSE_VALUES
    )


PURPOSE_DEFAULTS: tuple[Purpose, ...] = _purpose_defaults()
"""Стандартные цели поездок (параметры модели из Takt, playtakt.app)."""


@dataclass(frozen=True, slots=True)
class VehicleSpec:
    """Параметры подвижного состава для эксплуатационных KPI маршрута.

    Характеристики взяты из модели Takt (fleet table): вместимость, затраты,
    время остановки и оборота, ограничение по частоте (``track_tph`` —
    макс. поездов/час на линии).
    """

    key: str
    capacity: int = 90
    opex_per_veh_km: float = 5.0
    veh_cost_day: float = 250.0
    dwell_s: float = 20.0
    dwell_per_pax_s: float = 2.0
    jitter_s: float = 90.0
    turnback_s: float = 60.0
    track_tph: float = 90.0
    access_m: float = 500.0
    speed_kmh: float = 18.0
    cost_per_km_eur: float = 0.4
    capex_eur_per_km: float = 0.0


_VEHICLE_VALUES: list[tuple[str, int, float, float, float, float, float, float, float, float, float, float, float]] = [
    ("bus", 90, 5.0, 250.0, 20.0, 2.0, 90.0, 60.0, 90.0, 500.0, 18.0, 0.4, 0.0),
    ("tram", 250, 9.0, 900.0, 25.0, 0.6, 60.0, 90.0, 40.0, 600.0, 19.0, 9.0, 12e6),
    ("metro", 750, 14.0, 3200.0, 30.0, 0.15, 15.0, 150.0, 30.0, 800.0, 70.0, 32.0, 60e6),
    ("rail", 1000, 22.0, 5200.0, 45.0, 0.3, 25.0, 300.0, 20.0, 1500.0, 58.0, 22.0, 80e6),
]


def _vehicle_defaults() -> dict[str, VehicleSpec]:
    return {
        key: VehicleSpec(key=key, capacity=c, opex_per_veh_km=o, veh_cost_day=v,
                         dwell_s=dw, dwell_per_pax_s=dp, jitter_s=jt, turnback_s=tb,
                         track_tph=tt, access_m=am, speed_kmh=sp, cost_per_km_eur=ck,
                         capex_eur_per_km=cx)
        for key, c, o, v, dw, dp, jt, tb, tt, am, sp, ck, cx in _VEHICLE_VALUES
    }


VEHICLE_DEFAULTS: dict[str, VehicleSpec] = _vehicle_defaults()
"""Парк по типам транспорта (bus/tram/metro/rail) из модели Takt."""

_MODE_TO_VEHICLE = {
    "bus": "bus",
    "trolleybus": "bus",
    "electrobus": "bus",
    "minibus": "bus",
    "tram": "tram",
    "metro": "metro",
    "rail": "rail",
    "train": "rail",
    "railroad": "rail",
    "funicular": "bus",
    "cable": "bus",
    "monorail": "metro",
    "water": "bus",
}


def vehicle_spec_for_route_type(route_type: object) -> VehicleSpec:
    """Спецификация парка по строке/перечислению типа маршрута."""
    key = _MODE_TO_VEHICLE.get(str(route_type).strip().lower(), "bus")
    return VEHICLE_DEFAULTS[key]


@dataclass(frozen=True, slots=True)
class LineResult:
    """Эксплуатационные KPI одной линии (при заданном ``headway_min``).

    ``cycle_km``/``cycle_min`` — полный оборот (туда+обратно); ``fleet`` —
    потребный парк ``ceil(cycle_min / headway)``; ``veh_km_day`` —
    пробег за сутки; ``opex_day`` — операционные затраты; ``revenue_day`` —
    выручка от тарифа, отнесённая на линию; ``crowding`` — средний
    коэффициент заполнения; ``min_headway`` — минимально возможный интервал
    (ограничение инфраструктуры ``track_tph``).
    """

    route_id: int
    route_name: str
    mode: str
    trips: float = 0.0
    cycle_km: float = 0.0
    cycle_min: float = 0.0
    fleet: float = 0.0
    veh_km_day: float = 0.0
    opex_day: float = 0.0
    revenue_day: float = 0.0
    capital_cost_eur: float = 0.0
    capex_day: float = 0.0
    crowding: float = 0.0
    min_headway: float = 0.0
    passenger_km: float = 0.0
    crowded_passenger_km: float = 0.0
    excess_passenger_km: float = 0.0
    severe_passenger_km: float = 0.0
    extreme_passenger_km: float = 0.0


@dataclass(frozen=True, slots=True)
class ModeChoiceConfig:
    """Параметры выбора режима поездки «транзит / авто / пешком / eBike».

    Все стоимости приводятся к обобщённым минутам: денежные элементы
    (тариф, топливо, парковка) пересчитываются через ``vot_per_eur_s`` —
    секунды ценности времени за 1 EUR (в Takt ≈ 360–570 с/EUR).

    Значения по умолчанию — дефолты Takt (``va/wa/ga/Pa``); синхронизация
    с ``passenger_flow.takt`` проверяется тестом (прямой импорт невозможен
    из-за циклической зависимости).
    """

    # Takt noCar: доля населения без авто до поправки Aa.
    car_no_car_share: float = TAKT_NO_CAR_SHARE
    car_no_car_factor: float = TAKT_NO_CAR_FACTOR
    car_parking_min: float = TAKT_CAR_PARKING_S / 60.0
    car_cost_per_km_eur: float = TAKT_CAR_COST_PER_KM_EUR
    car_parking_eur: float = TAKT_CAR_PARK_EUR
    car_circuity: float = TAKT_CAR_CIRCUITY
    car_speed_kmh: float = TAKT_CAR_SPEED_KMH
    # Пешком: Takt использует Ze × 1.25 и скорость 1.33 м/с.
    walk_speed_mps: float = TAKT_WALK_SPEED_MPS
    walk_circuity: float = TAKT_WALK_CIRCUITY
    # Ценность времени и тариф: fare = max(base, min_fare, base + per_km×км),
    # при fare_cap_eur > 0 результат ограничивается сверху.
    vot_per_eur_s: float = TAKT_VOT_S_PER_EUR
    fare_base_eur: float = TAKT_FARE_BASE_EUR
    fare_per_km_eur: float = TAKT_FARE_PER_KM_EUR
    fare_cap_eur: float = TAKT_FARE_CAP_EUR
    min_fare_eur: float = 0.0
    # Электровелосипед: активен только при two_wheel_share > 0 и для
    # расстояний не дальше two_wheel_reach_m (по мотивам Takt).
    two_wheel_share: float = TAKT_TWO_WHEEL_SHARE
    two_wheel_speed_mps: float = TAKT_TWO_WHEEL_SPEED_MPS
    two_wheel_reach_m: float = TAKT_TWO_WHEEL_REACH_M
    two_wheel_per_km_eur: float = TAKT_TWO_WHEEL_PER_KM_EUR
    two_wheel_fixed_s: float = TAKT_TWO_WHEEL_FIXED_S
    two_wheel_circuity: float = TAKT_TWO_WHEEL_CIRCUITY
    # Постоянная поправка стоимости транзита Wo в секундах.
    rider_bias_s: float = TAKT_RIDER_BIAS_S
    rest_base_speed_kmh: float = TAKT_REST_BASE_SPEED_KMH
    rest_cont_speed_kmh: float = TAKT_REST_CONT_SPEED_KMH
    rest_access_s: float = TAKT_REST_ACCESS_S
    rest_wait_s: float = TAKT_REST_WAIT_S
    rest_circuity: float = TAKT_REST_CIRCUITY


@dataclass(frozen=True, slots=True)
class FlowResult:
    """Итоговый результат расчёта пассажиропотока."""

    route_flows: tuple[RouteFlowResult, ...]
    stop_flows: tuple[StopFlowResult, ...]
    total_trips: float
    assigned_trips: float
    routes_served: int
    intrazonal_trips: float = 0.0
    car_trips: float = 0.0
    walk_trips: float = 0.0
    two_wheel_trips: float = 0.0
    rest_trips: float = 0.0
    period_flows: tuple[PeriodFlow, ...] = ()
    line_results: tuple[LineResult, ...] = ()
    revenue_day: float = 0.0
    opex_day: float = 0.0
    capex_day: float = 0.0
    fleet_total: float = 0.0