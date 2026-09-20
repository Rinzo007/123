"""Эталонные константы модели Takt (playtakt.app).

Все значения перенесены из минифицированных скриптов движка
``D:\\Programs\\found_scripts\\scripts``:

- ``bc1e6aad22337e5eeaa8.js`` — генерация поездок, тариф, ожидание,
  надёжность, логит, полосы межостановочных расстояний, периоды, цели,
  парк (fleet table);
- ``64b3e62b167b2f7dcfa2.js`` — присваивание: ходьба и пересадки, MSA,
  перечисление маршрутов, модель режимов (мо/авто/прочее), сооружения.

Модуль — единственный источник значений: расчётный код импортирует
константы отсюда, тесты сверяют с ними таблицы ``PURPOSE_DEFAULTS`` /
``VEHICLE_DEFAULTS``.
"""

from __future__ import annotations

import math

from .models import Period

# ---------------------------------------------------------------------------
# Ожидание на посадке — Ze (bc1e6aad22337e5eeaa8.js, ок. "@2731")
# ---------------------------------------------------------------------------
_TAKT_WAIT_LINEAR_LIMIT_MIN = 12.0  # до 12 мин — половина такта
_TAKT_WAIT_FLOOR_MIN = 6.0          # пол «редкого» ожидания
_TAKT_WAIT_EXTRA_PER_MIN = 0.1      # +0.1 мин за каждый дополнительный мин такта
_TAKT_DAY_S = 86400.0               # ge: «необслуживается» (сек/сутки)

# ---------------------------------------------------------------------------
# Надёжность расписания — He(e,t) = max(20 с, hypot(jitter_s, 0.4×h_с))
# ---------------------------------------------------------------------------
_TAKT_RELIABILITY_FLOOR_S = 20.0    # we: нижний предел штрафа, секунды
_TAKT_RELIABILITY_JITTER_FACTOR = 0.4  # be: вклад интервала движения

# ---------------------------------------------------------------------------
# Модель режимов — va/wa/ga/Pa/Aa (64b3e62b, ок. "@10500"). Дефолты
# применяются, если в model.json нет блоков mobility/car/rest/votSPerEur.
# ---------------------------------------------------------------------------
_TAKT_MOBILITY_DEFAULTS: dict[str, float] = {
    "noCar": 0.35,             # доля домохозяйств без авто
    "twoWheelShare": 0.3,      # доля двухколёсных (велосипед/самокат)
    "twoWheelSpeed": 4.2,      # скорость двухколёсного, м/с
    "twoWheelReachM": 7000.0,  # предел дальности двухколёсного, м
    "twoWheelPerKm": 0.03,     # стоимость двухколёсного, евро/км
}
_TAKT_CAR_DEFAULTS: dict[str, float] = {
    "costPerKm": 0.25,         # стоимость авто, евро/км
    "parkEur": 1.5,            # стоимость парковки, евро
    "parkingS": 240.0,         # поиск парковки, с
}
_TAKT_REST_DEFAULTS: dict[str, float] = {
    "baseSpeed": 3.6,          # базовая скорость, км/ч
    "contSpeed": 5.0,          # скорость «прочих» (самокат), км/ч
    "accessS": 420.0,          # доступ + ожидание, с
    "waitS": 240.0,            # ожидание, с
    "circuity": 1.3,           # извилистость маршрута
}
_TAKT_NO_CAR_FACTOR = 0.78     # Aa: доля «без авто» × Aa → отказ от авто
_TAKT_VOT_S_PER_EUR = 360.0    # Pa: значение времени по умолчанию, с/евро
_TAKT_RIDER_BIAS_S = 0.0       # Wo: постоянный сдвиг стоимости транзита, с

# ---------------------------------------------------------------------------
# Тариф — da/pa/ha/Y/me/Xe (bc1e6aad и 64b3e62b, ок. "@2549"/"@10560"):
# ``fare = min(max(3, base), base + perKm * dist_km)``.
# ---------------------------------------------------------------------------
_TAKT_FARE_BASE_EUR = 0.6
_TAKT_FARE_PER_KM_EUR = 0.12
_TAKT_FARE_CAP_EUR = 3.0             # pa/me: потолок в min(max(pa, base), base+d·perKm)
_TAKT_FARE_EPS = 1e-6                # Ge: порог «тариф не изменён»
_TAKT_FARE_REVENUE_FACTOR = 0.9      # Xe/ha: учёт доли выручки


def _takt_fare_eur(base: float, per_km: float, dist_m: float) -> float:
    """Тариф Takt: ``min(max(cap, base), base + per_km * d_km)`` (qa/ma)."""
    return min(
        max(_TAKT_FARE_CAP_EUR, float(base)),
        float(base) + float(per_km) * float(dist_m) / 1000.0,
    )

# ---------------------------------------------------------------------------
# Логит — Ye/Qe (bc1e6aad, ок. "@2731"): p = 1/(1+exp(-1.702×Δ/t)).
# Масштаб 1.702 — константа Gumbel-преобразования, приведённая к
# «процентам» полезности; Qe — обратная (log-sum) функция для эластичности.
# ---------------------------------------------------------------------------
_TAKT_LOGIT_SCALE = 1.702
_TAKT_LOGIT_TEMP_FLOOR = 1.0
_TAKT_ELASTIC_CUTOFF = 0.25          # порог, выше которого спрос обнуляется


def _takt_logit_prob(utility_diff: float, temp: float) -> float:
    """Вероятность выбора альтернативы при разнице полезности Δ (мин)."""
    return 1.0 / (
        1.0
        + math.exp(
            -_TAKT_LOGIT_SCALE * utility_diff / max(_TAKT_LOGIT_TEMP_FLOOR, float(temp))
        )
    )


def _takt_logit_shift(utility_diff: float, temp: float) -> float:
    """Обратная логит-функция Qe: ожидаемая полезность выбора из двух.
    При ``r >= 0.25`` (спрос за пределами эластичности) возвращает 0.
    """
    denom = 1.702 * max(_TAKT_LOGIT_TEMP_FLOOR, float(temp))
    r = float(utility_diff) / denom
    if r >= _TAKT_ELASTIC_CUTOFF:
        return 0.0
    side = (1.0 + (1.0 - 4.0 * r) ** 0.5) / 2.0
    return utility_diff / 1.702 * math.log(side / (1.0 - side))


# ---------------------------------------------------------------------------
# Полосы межостановочных расстояний — Me + вердикт et (bc1e6aad, ок. "@3943")
# ---------------------------------------------------------------------------
STOP_SPACING_BANDS: dict[str, tuple[int, int]] = {
    "bus": (300, 500),
    "tram": (400, 700),
    "metro": (800, 1500),
    "rail": (2000, 5000),
}
_SPACING_DENSITY_BASE = 0.35         # b: застройка ниже этого — «разреженная»
_SPACING_DENSITY_SPAN = 0.65         # нормализация плотности (0..1 после 0.35)
_SPACING_SHRINK_LO = 0.28            # сужение нижней границы под плотность
_SPACING_SHRINK_HI = 0.14            # сужение верхней границы под плотность
_SPACING_TOO_CLOSE_FACTOR = 0.55     # dist < lo×0.55 — «too close»
_SPACING_WIDE_FACTOR = 1.9           # dist > hi×1.9 — «too far», между — wide
_SPACING_DENSE_PREFIX = 0.7          # плотность, при которой пояснение «плотно»
_SPACING_THIN_PREFIX = 0.3           # плотность, при которой пояснение «редко»

# ---------------------------------------------------------------------------
# Периоды суток — tt (bc1e6aad, ок. "@3943"); out+ret — доли поездок туда/обратно
# ---------------------------------------------------------------------------
_TAKT_PERIOD_HOURS = {
    "early": 2,   # 04-06
    "am": 3,      # 06-09
    "mid": 6,     # 09-15
    "pm": 4,      # 15-19
    "eve": 5,     # 19-24
}

TAKT_PERIODS: tuple[Period, ...] = (
    Period("early", "04-06", out=0.06, ret=0.01),
    Period("am", "06-09", out=0.60, ret=0.06),
    Period("mid", "09-15", out=0.20, ret=0.18),
    Period("pm", "15-19", out=0.10, ret=0.55),
    Period("eve", "19-24", out=0.04, ret=0.20),
)

# ---------------------------------------------------------------------------
# Цели поездок — rt (bc1e6aad, ок. "@4269")
# тождественны PURPOSE_DEFAULTS (модель wikiroutes); здесь — справка.
# ---------------------------------------------------------------------------
_TAKT_PURPOSE_TABLE: dict[str, tuple[str, float, float, int]] = {
    # key: (label, trips_per_res, d0_m, k)
    # k — число дистанционных направлений движка xn: на каждый из 4
    # диапазонов [0, d0, 2.5d0, 6d0, inf] берётся max(1, round(k/4)) лучших
    # аттракторов; доля поездок на дистанцию d ~ exp(-d/d0) (не (1+d/d0)^-k).
    "edu": ("School or campus", 0.16, 1600.0, 6),
    "health": ("Hospital or clinic", 0.06, 3000.0, 6),
    "shop": ("Shops", 0.34, 2000.0, 6),
    "air": ("Airport", 0.03, 14000.0, 2),
    "night": ("Bar, cafe or venue", 0.22, 3000.0, 6),
}

# ---------------------------------------------------------------------------
# Точки спроса и базовые (fallback) слои OD — 64b3e62b, ок. "@109300".
# Точка спроса: [lon, lat, население, рабочие места]. Если purposes.json не
# загружен, движок строит два слоя: errands (профиль Kc) и leisure (Wc).
# ---------------------------------------------------------------------------
_TAKT_POINT_FIELDS: tuple[str, ...] = ("lon", "lat", "pop", "jobs")
_TAKT_BASELINE_OD_LAYERS: dict[str, dict[str, object]] = {
    "errands": {"trips_per_res": 0.5, "d0_m": 1800.0, "k": 10, "attract": "jobs"},
    "leisure": {
        "trips_per_res": 0.32,
        "d0_m": 3200.0,
        "k": 10,
        "attract": "0.6*jobs + 0.4*pop",
    },
}
_TAKT_BASELINE_PERIODS: dict[str, dict[str, tuple[float, ...]]] = {
    # Kc — с утренним пиком (errands), Wc — вечерние (leisure).
    "errands": {
        "out": (0.02, 0.08, 0.55, 0.25, 0.10),
        "ret": (0.01, 0.04, 0.45, 0.35, 0.15),
    },
    "leisure": {
        "out": (0.01, 0.03, 0.15, 0.31, 0.50),
        "ret": (0.01, 0.02, 0.10, 0.27, 0.60),
    },
}

# ---------------------------------------------------------------------------
# Координация расписаний пересадок — jo/hs (085f71988f12f8f584de.js).
# ``hs(c,v,S,T,j,N)``: ожидание пересадки с учётом расписания двух линий.
# ``jo(t,e,s,o,n,i,r)``: расчёт скоординированного ожидания на стыке.
# ---------------------------------------------------------------------------
_TAKT_HOLDBACK_FLOOR_S = 20.0        # Ia: нижний предел half-width
_TAKT_HOLDBACK_WALK_FACTOR = 0.4      # Ea: вклад ходьбы в half-width
_TAKT_HOLDBACK_SCALE = 1.702          # вес logit для вероятности поимки


def _takt_hold_prob(wait_s: float, halfwidth_s: float) -> float:
    """Ra(b,p) = 1/(1+exp(-1.702·b/max(1,p))): вероятность уловить поезд.

    ``b`` — ожидание (с), ``p`` — half-width надёжности (с).
    """
    return 1.0 / (
        1.0 + math.exp(
            -_TAKT_HOLDBACK_SCALE * float(wait_s)
            / max(1.0, float(halfwidth_s))
        )
    )


def _takt_reliability_halfwidth_s(jitter_s: float, walk_s: float) -> float:
    """Ca(t,e) = max(Ia, hypot(jitter, 0.4·walk)): half-width надёжности.

    ``jitter_s`` — джиттер вида транспорта (с), ``walk_s`` — ходьба (с).
    """
    return max(
        _TAKT_HOLDBACK_FLOOR_S,
        math.hypot(float(jitter_s), _TAKT_HOLDBACK_WALK_FACTOR * float(walk_s)),
    )


def _takt_po_seconds(headway_min: float) -> float:
    """Po(t): ожидание на посадке (с) по интервалу (мин).

    ≤12 мин: headway·60/2; иначе (6 + 0.1·headway)·60.
    """
    t = float(headway_min)
    if t <= 0:
        return _TAKT_DAY_S
    if t <= _TAKT_WAIT_LINEAR_LIMIT_MIN:
        return t * 60.0 / 2.0
    return (_TAKT_WAIT_FLOOR_MIN + _TAKT_WAIT_EXTRA_PER_MIN * t) * 60.0


def _takt_jo(
    t_min: float,
    e_min: float,
    s_sec: float,
    o_sec: float,
    n_sec: float,
    jitter_s: float,
    default_wait_s: float,
) -> float:
    """jo(t,e,s,o,n,i,r): скоординированное ожидание на пересадке (сек).

    ``t_min`` — интервал первой линии (мин, от которой уходим),
    ``e_min`` — интервал второй линии (мин, на которую садимся),
    ``s_sec`` / ``o_sec`` — фазы +累计 время до остановки (сек),
    ``n_sec`` — время ходьбы между платформами (с),
    ``jitter_s`` — джиттер вида первой линии (с),
    ``default_wait_s`` — дефолт ожидание второй линии (с, при неприменимости).
    """
    t = float(t_min)
    e = float(e_min)
    if t <= 0 or e <= 0 or (t % e != 0 and e % t != 0):
        return default_wait_s
    a = e * 60.0
    f = t * 60.0
    u = o_sec - (s_sec + n_sec)
    p = _takt_reliability_halfwidth_s(jitter_s, n_sec)
    l = 1 if f % a == 0 else math.floor(a / f + 0.5)
    h = 0.0
    for y in range(int(l)):
        b = ((u - y * f) % a + a) % a
        d = _takt_hold_prob(b, p)
        h += b + (1.0 - d) * a
    return h / l


def _takt_hs(
    headway_from_min: float,
    headway_to_min: float,
    cum_from_s: float,
    cum_to_s: float,
    walk_s: float,
    jitter_from_s: float,
    default_wait_s: float,
) -> float:
    """hs: ожидание пересадки с расписанием (сек), обёртка над jo.

    ``cum_from_s`` / ``cum_to_s``: ``(phase * 60 + Yt(stop))``,
    ``walk_s``: ходьба между платформами (с),
    ``jitter_from_s``: джиттер вида линии, с которой приходим,
    ``default_wait_s``: ожидание «без координации» (с).
    """
    t = float(headway_from_min)
    e = float(headway_to_min)
    if t <= 0 or e <= 0 or (t % e != 0 and e % t != 0):
        return default_wait_s
    return _takt_jo(
        t, e,
        cum_from_s, cum_to_s, walk_s,
        jitter_from_s, default_wait_s,
    )


# ---------------------------------------------------------------------------
# Парк — таблица I (bc1e6aad, ок. "@653"). Скорости рядов — км/ч как в исходнике
# (в движке переводятся м/с множителем y = e/3.6).
# ---------------------------------------------------------------------------
TAKT_FLEET: dict[str, dict[str, object]] = {
    "bus": {
        "capacity": 90,
        "opex_per_veh_km": 5.0,
        "veh_cost_day": 250.0,
        "dwell_s": 20.0,
        "dwell_per_pax_s": 2.0,
        "jitter_s": 90.0,
        "turnback_s": 60.0,
        "track_tph": 90.0,
        "infill_m": 0.4,
        "access_m": 500.0,
        "max_cls": 3,
        "pax_per_m": 6.0,
        "platform_m": 0.0,
        "platform_cost_m": 0.0,
        "a_lat": 1.1,
        "accel": 1.2,
        "r_comfort": 15.0,
        "r_min": 8.0,
        "rows": {"mixed": {"kmh": 18.0, "cost_per_km": 0.4}, "reserved": {"kmh": 23.0, "cost_per_km": 2.5}},
        "default_row": "mixed",
    },
    "tram": {
        "capacity": 250,
        "opex_per_veh_km": 9.0,
        "veh_cost_day": 900.0,
        "dwell_s": 25.0,
        "dwell_per_pax_s": 0.6,
        "jitter_s": 60.0,
        "turnback_s": 90.0,
        "track_tph": 40.0,
        "infill_m": 2.5,
        "access_m": 600.0,
        "max_cls": 2,
        "pax_per_m": 6.5,
        "platform_m": 40.0,
        "platform_cost_m": 0.031,
        "a_lat": 0.9,
        "accel": 1.2,
        "r_comfort": 30.0,
        "r_min": 18.0,
        "rows": {
            "mixed": {"kmh": 19.0, "cost_per_km": 9.0},
            "reserved": {"kmh": 25.0, "cost_per_km": 18.0},
            "grade": {"kmh": 33.0, "cost_per_km": 85.0},
        },
        "default_row": "mixed",
    },
    "metro": {
        "capacity": 750,
        "opex_per_veh_km": 14.0,
        "veh_cost_day": 3200.0,
        "dwell_s": 30.0,
        "dwell_per_pax_s": 0.15,
        "jitter_s": 15.0,
        "turnback_s": 150.0,
        "track_tph": 30.0,
        "infill_m": 60.0,
        "access_m": 800.0,
        "max_cls": 3,
        "pax_per_m": 8.0,
        "platform_m": 100.0,
        "platform_cost_m": 0.3,
        "a_lat": 0.8,
        "accel": 1.0,
        "r_comfort": 150.0,
        "r_min": 90.0,
        "rows": {
            "reserved": {"kmh": 70.0, "cost_per_km": 32.0},
            "elevated": {"kmh": 70.0, "cost_per_km": 62.0},
            "grade": {"kmh": 70.0, "cost_per_km": 120.0},
        },
        "default_row": "reserved",
    },
    "rail": {
        "capacity": 1000,
        "opex_per_veh_km": 22.0,
        "veh_cost_day": 5200.0,
        "dwell_s": 45.0,
        "dwell_per_pax_s": 0.3,
        "jitter_s": 25.0,
        "turnback_s": 300.0,
        "track_tph": 20.0,
        "infill_m": 35.0,
        "access_m": 1500.0,
        "max_cls": 3,
        "pax_per_m": 7.0,
        "platform_m": 140.0,
        "platform_cost_m": 0.125,
        "a_lat": 0.65,
        "accel": 0.8,
        "r_comfort": 400.0,
        "r_min": 150.0,
        "rows": {
            "reserved": {"kmh": 58.0, "cost_per_km": 22.0},
            "elevated": {"kmh": 78.0, "cost_per_km": 48.0},
            "grade": {"kmh": 78.0, "cost_per_km": 95.0},
        },
        "default_row": "reserved",
    },
}

# ---------------------------------------------------------------------------
# Ходьба и пересадки — 64b3e62b167b2f7dcfa2.js: ae, Za, Uo, ec, kn, Ko, tc
# ---------------------------------------------------------------------------
_TAKT_WALK_SPEED_MPS = 1.33           # ae: скорость пешехода, м/с
_TAKT_TRANSFER_MAX_WALK_M = 800.0     # Za: максимум пешей пересадки, м
_TAKT_WALK_BASE_S = 405.0             # Uo: базовая стоимость выхода, с
_TAKT_WALK_PER_M_S = 0.25             # ec: добавка за метр, с
_TAKT_WALK_DISUT_PER_M_S = 1.0        # kn: дискомфорт за метр ходьбы, с/м
_TAKT_TRANSFER_WAIT_FACTOR = 1.0      # Ko: множитель ожидания на пересадке
_TAKT_TURNBACK_FIXED_S = 405.0        # tc: резерв оборота на конечной, с
_TAKT_WALK_CIRCUITY = 1.25            # извилистость полного пешего пути Ze

# ---------------------------------------------------------------------------
# Crowding (перегрузка) — tn/er/Ua/ti (085f71988f12f8f584de.js).
# Загрузка сегмента nt = max(segP, segR) / (часы периода × частота × вместимость);
# классы: crowded при nt > 1, severe при nt >= 2, extreme при nt >= 4;
# excessPassengerKm = только превышение над вместимостью (nt > 1).
# ---------------------------------------------------------------------------
_TAKT_CROWD_THRESH_LOAD = 0.85        # tn: начало роста множителя в пути
_TAKT_CROWD_EFF_CAP_LOAD = 1.5        # tn: насыщение роста (до 1.5)
_TAKT_CROWD_MULT_MAX = 2.2            # tn: предел множителя (1 + 0.65×2.2)
_TAKT_CROWD_COMFORT_ARM = 0.25        # er: плечо «комфортного» множителя
_TAKT_CROWD_COMFORT_ARM_CAP = 0.9     # er: потолок добавки (0.9)
_TAKT_CROWD_LOAD_FLOOR = 10.0         # er: нижний предел загрузки в знаменателе
_TAKT_CROWDED_LOAD_FACTOR = 1.0       # nt > 1 → crowded/excess
_TAKT_SEVERE_LOAD_FACTOR = 2.0        # nt >= 2 → severe
_TAKT_EXTREME_LOAD_FACTOR = 4.0       # nt >= 4 → extreme


def _takt_crowding_ride_mult(load_factor: float) -> float:
    """tn(c) = 1 + max(0, min(c, 1.5) − 0.85) × 2.2: множитель времени в пути.

    Растёт только при загрузке c > 0.85, насыщается на 1.5 (первый
    «избыточный» множитель движка, используется для swipe-cxPre).
    """
    c = float(load_factor)
    return (
        1.0
        + max(0.0, min(c, _TAKT_CROWD_EFF_CAP_LOAD) - _TAKT_CROWD_THRESH_LOAD)
        * _TAKT_CROWD_MULT_MAX
    )


def _takt_crowding_wait_mult(load_factor: float) -> float:
    """ti(load) = max(1, lf): множитель ожидания на переполненной остановке."""
    return max(1.0, float(load_factor))


def _takt_crowding_comfort(load_factor: float, r_comfort: float) -> float:
    """er(mode, lf): «комфортный» множитель загрузки с опорой rComfort парка.

    ``lf >= rComfort`` → 1 (комфортно); иначе 1 + min(0.9, (rComfort/max(10,lf)
    − 1) × 0.25). Характерный «изгиб» Takt для единицы перегруженности.
    """
    if float(load_factor) >= float(r_comfort):
        return 1.0
    return 1.0 + min(
        _TAKT_CROWD_COMFORT_ARM_CAP,
        (
            float(r_comfort) / max(_TAKT_CROWD_LOAD_FLOOR, float(load_factor))
            - 1.0
        )
        * _TAKT_CROWD_COMFORT_ARM,
    )


def _takt_crowding_km_classes(
    load_factor: float,
    pax: float,
    seg_km: float,
    capacity: float,
    runs: float,
) -> tuple[float, float, float, float, float]:
    """Пассажиро-километры и классы перегруженности сегмента (по Ua).

    Возвращает (passengerKm, crowdedKm, excessKm, severeKm, extremeKm) для
    сегмента с нагрузкой ``pax`` и загрузкой ``nt = pax/(capacity×runs)``.
    ``excessKm`` — только превышение над вместимостью: max(0, pax − cap×runs).
    """
    nt = load_factor
    pkm = float(pax) * float(seg_km)
    crowded = pkm if nt > _TAKT_CROWDED_LOAD_FACTOR else 0.0
    excess = (
        max(0.0, float(pax) - float(capacity) * max(float(runs), 1e-9)) * seg_km
        if nt > _TAKT_CROWDED_LOAD_FACTOR
        else 0.0
    )
    severe = pkm if nt >= _TAKT_SEVERE_LOAD_FACTOR else 0.0
    extreme = pkm if nt >= _TAKT_EXTREME_LOAD_FACTOR else 0.0
    return pkm, crowded, excess, severe, extreme


# ---------------------------------------------------------------------------
# MSA-присваивание и перечисление маршрутов (64b3e62b…) — справка
# ---------------------------------------------------------------------------
_TAKT_MSA_GAP = 0.01                  # остановка при разрыве <= 1%
_TAKT_MSA_STEP_START = 1              # первый шаг усреднения 1/iteration
_TAKT_MAX_LEGS = 4                    # Ha: макс. число ножек пути
_TAKT_ALTS = 3                        # Ir/jc: альтернативные линии у остановки
_TAKT_ALT_DETOUR_FACTOR = 1.25        # порог «заметного» крюка при выборе альт.
_TAKT_ALT_DETOUR_FIXED_S = 120.0      # +120 с фиксированного запаса
# Радиус доступности остановки — по типу транспорта (поле ``accessM``
# таблицы парка ниже): bus 500 / tram 600 / metro 800 / rail 1500 м. Он же
# передаётся в ``VehicleSpec.access_m`` и используется при привязке зон
# (движок: линия — кандидат, если ``nearD[c] <= access``).
_TAKT_INTERCHANGE_MATCH_M = 800.0       # An/Za: радиус совмещения остановок пересадки, м
_TAKT_PARALLEL_MIN_OD_ROWS = 1200       # Na: порог OD-строк для параллельной оценки (t < Na → serial)
_TAKT_M_PER_DEG_LAT = 111_000.0         # re: метров на градус широты
_TAKT_M_PER_DEG_LON_DEFAULT = 68_000.0  # wn: метров на градус долготы (старт)
_TAKT_M_PER_DEG_LON_EQUATOR = 111_320.0  # ea: wn = 111320*cos(lat), |lat| <= 85
_TAKT_EARTH_RADIUS_M = 6_371_000.0      # Do: радиус Земли для гаверсинуса (At)
_TAKT_ACCESS_WALK_M = 62.0              # дистанция, при которой берётся Sn, м
_TAKT_ACCESS_SURCHARGE_S = 420.5        # Sn = vn(62) = 405 + 0.25 * 62, с

# ---------------------------------------------------------------------------
# Факторы стоимости путевых сооружений — nc/rc/Jo/oc (64b3e62b, ок. "@57420").
# Удорожание рельсовых путей по типу профиля и площади застройки; только rail.
# ---------------------------------------------------------------------------
_TAKT_TRACK_DENSITY_FACTORS = {"reserved": 3.0, "elevated": 1.0}     # nc
_TAKT_TRACK_SLOPE_FACTORS = {"reserved": 8.0, "elevated": 1.5, "grade": 2.5}  # rc
_TAKT_BUILDING_AREA_FACTORS = {"reserved": 10.0, "elevated": 4.0}    # Jo (/10)
_TAKT_BUILDING_AREA_PER_M2 = 0.004      # oc: ед. стоимости на м² застройки

# ---------------------------------------------------------------------------
# Пространственные сетки, допуски и авто-стоимость — 64b3e62b
# ---------------------------------------------------------------------------
_TAKT_SPATIAL_GRID_DEG = 0.006          # Vo: ячейка кэша доступности
_TAKT_COVERAGE_GRID_DEG = 0.003         # qe: ячейка растров покрытия
_TAKT_SEGMENT_GRID_DEG = 0.004          # ke: ячейка индекса сегментов
_TAKT_SNAP_GRID_DEG = 1e-4              # Pn: ячейка снэпа сегментов
_TAKT_STOP_MERGE_M = 150.0              # ni: слияние близких вариантов остановок, м
_TAKT_ACCESS_STREET_M = 150.0           # CROSS_M: предел выхода на улицу, м
_TAKT_CAR_CLASS_COST = (1.0, 1.25, 1.7, 2.6, 0.55)  # CAR_COST по классам улиц

# Справочные внутренности движка без аналога в wikiroutes
# (оставлены значениями из исходника; не используются в расчёте).
_TAKT_ENGINE_INTERNALS: dict[str, float | int] = {
    "Ao": 1.0,      # множитель стоимости километра по типу пути (fa)
    "An": 800.0,    # An/Za: радиус совмещения остановок пересадки (= кэш-ячейка остановок ~800 м)
    "Bc": 300.0,    # шаг сетки зон «30 мин пешком», м
    "En": 5,        # подвыборка вариантов при перечислении
    "Hc": 5,        # периодов в расписании строки
    "jc": 3,        # мелкая подвыборка OD-строк быстрого прохода (Js по умолч.)
    "ka": 240.0,    # горизонт поиска, мин
    "ks": 360.0,    # базовый хвост оборота, с
    "lr": 9.0,      # грубая подвыборка OD-строк упрощённого прохода (Js(..., lr))
    "mt": 1.8,      # потолок пикового множителя загрузки (yt = min(mt, 1+nt·max(0, N/T−1)))
    "No": 600.0,    # добавка оборота на конечных, с (N = closed ? j + No/2 : 2j + No)
    "ns": 1.5,      # коэффициент на извилистость
    "nt": 0.6,      # чувствительность пикового множителя загрузки к перегрузке
    "qc": 6000.0,   # сглаживание загрузки (в источнике 6e3)
    "Vc": 25.0,     # скорость «дали в минутах» для авто, км/ч
    "Wo": 0.0,      # смещение весов
    "Zo": 1414744916,  # seed хэша кэша
}


# --- краткая справка о происхождении для отладки ---------------------------
_TAKT_SOURCES = (
    ("bc1e6aad22337e5eeaa8.js", "Ze/He/Y/me/Ye/Qe/Me/tt/rt/I/Xe"),
    ("64b3e62b167b2f7dcfa2.js", "ae/Za/Uo/ec/kn/Ko/tc/Ha/Ir/va/wa/ga/xn/Kc/Wc"),
)

__all__ = [
    "STOP_SPACING_BANDS",
    "TAKT_FLEET",
    "TAKT_PERIODS",
    "_takt_hold_prob",
    "_takt_hs",
    "_takt_jo",
    "_takt_po_seconds",
    "_takt_reliability_halfwidth_s",
]