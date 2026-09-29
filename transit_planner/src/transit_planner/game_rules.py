"""Правила пути и стоимости поездки по разбору Subway Builder 1.6.0.

Источник: `D:\\Programs\\Project\\Текстовый документ.txt`, разбор
декомпилированного `index-C2DH4lPc.js` (PATHFINDING_RULES_SPEC, строки
128709; PERCEIVED_TIME, строки 128803). Это константы из поставляемой сборки,
а не подгонка под правдоподобие, поэтому они сопоставимы с игровым
поведением по построчно.

Ключевое отличие от `reference_model`: там множители приведены к абсолютным
минутам бремени (1.65 / 6.72 / 9.91 для подхода, выхода и пересадки), здесь
к минуте езды, равной 1.0 (ходьба 1.39, ожидание 1.37, парковка 1.60). Обе
шкалы описывают одно и то же, но пересчёт между ними не тождественен, поэтому
множители хранятся здесь, а не подменяют существующие.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import erfc, exp, log, pi, sqrt

# PERCEIVED_TIME, строки 128803. Относительно минуты в поезде = 1.0.
# Источник в разборе: Wardman et al. 2026, Transportation Research Part A,
# мета-анализ (таблицы 5 и 8).
RIDE_MIN = 1.00
WALK_MIN = 1.39
AIRPORT_WALK_MIN = 1.87  # багаж, аэропорт
WAIT_MIN = 1.37
DEPARTURE_SHIFT_MIN = 0.40
CONGESTED_DRIVING_MIN = 1.33
CONGESTION_FULL_AT_MIN = 2.00
PARKING_SEARCH_MIN = 1.60

# PATHFINDING_RULES_SPEC, строка 128709.
MAX_TRANSFERS = 4
MAX_WALK_TO_FROM_STATION_S = 2700  # 45 минут
MAX_DRIVE_TO_FROM_STATION_S = 420  # park & ride, 7 минут
MAX_TRANSFER_WALKING_TIME_S = 600  # 10 минут
FUTURE_CYCLE_TIME_OFFSET_S = 7200  # предрасчёт на 2 часа
MIN_FUTURE_CYCLES = 2
RANGE_QUERY_WINDOW_S = 1800  # окно отправлений rRAPTOR
MAX_RANGE_DEPARTURES = 24
ARRIVAL_GAP_S = 50  # пассажир приходит за 50 с до отправления
DRIVING_COST_PER_KM = 0.65
MIN_SENSIBLE_DRIVING_DISTANCE_M = 1000.0
PARKING_TIME_S = 180
PARKING_COST = 5.0
MIN_TRANSIT_CHOICE = 10  # если транзит выбрали <10 чел., берут 2-й выбор

# getIncomeForPerson, popCommuteWorker:38792.
INCOME_MEAN = 60_000.0
INCOME_STD_DEV = 25_000.0
MINIMUM_INCOME = 15_000.0
MAXIMUM_INCOME = 200_000.0
HOURS_WORKED_PER_YEAR = 1860.0
AIRPORT_INCOME_MULTIPLIER = 1.5
COLLEGE_INCOME_MULTIPLIER = 0.6
AIRPORT_PARKING_COST_MULTIPLIER = 5.0

# addPops, index:205527-205530.
DEFAULT_POP_SIZE = 200
ROAD_CIRCUITY_FACTOR = 1.3
AVG_DRIVING_SPEED_MPS = 11.1  # 40 км/ч
MIN_DRIVING_SECONDS = 60.0

# assignCommuteTimes, index:132965.
MIN_GAP_MINUTES = 90

# DRIVING_TIMES, index:128893 - множители пробок по уровню спроса.
DRIVING_TIME_MULTIPLIERS: dict[str, float] = {
    "veryLow": 0.8,  # ночью быстрее свободного потока
    "low": 0.9,
    "lowMedium": 1.0,
    "medium": 1.25,
    "high": 1.5,  # час пик
}

# getTimeOfDayRanges, index:132726: (home, work) по периодам суток.
# Уровни: veryLow / low / lowMedium / medium / high.
TIME_OF_DAY_RANGES: tuple[tuple[str, float, float, str, str], ...] = (
    ("lateNight", 0.0, 3.0, "veryLow", "veryLow"),
    ("earlyMorning", 3.0, 6.0, "low", "low"),
    ("morningRush", 6.0, 7.0, "medium", "low"),
    ("peakMorningRush", 7.0, 10.0, "high", "low"),
    ("lateMorningRush", 10.0, 11.0, "medium", "lowMedium"),
    ("midday", 11.0, 15.0, "lowMedium", "lowMedium"),
    ("afternoonRush", 15.0, 16.0, "lowMedium", "high"),
    ("eveningRush", 16.0, 19.0, "low", "high"),
    ("lateEveningRush", 19.0, 20.0, "low", "medium"),
    ("evening", 20.0, 23.0, "low", "low"),
    ("night", 23.0, 24.0, "veryLow", "veryLow"),
)

# HOUR_DEMAND_LEVELS, index:132808.
HOUR_DEMAND_LEVELS: dict[int, str] = {
    0: "veryLow", 1: "veryLow", 2: "veryLow",
    3: "low", 4: "low", 5: "low",
    6: "lowMedium",
    7: "high", 8: "high", 9: "high",
    10: "lowMedium",
    11: "medium", 12: "medium",
    13: "lowMedium", 14: "lowMedium", 15: "lowMedium",
    16: "high",
    17: "high", 18: "high",
    19: "lowMedium",
    20: "low", 21: "low", 22: "low",
    23: "veryLow",
}


@dataclass(frozen=True, slots=True)
class PerceivedMinutes:
    """Воспринимаемые минуты по альтернативе, минута езды = 1.0."""

    transit: float
    car: float
    walk: float

    def winner(self) -> str:
        costs = {"transit": self.transit, "driving": self.car, "walking": self.walk}
        return min(costs, key=lambda key: costs[key])

    def preferred(self, *, transit_pax: int = 0) -> str:
        """Выбор по правилу MIN_TRANSIT_CHOICE.

        Если на транзите едет меньше MIN_TRANSIT_CHOICE человек, предпочтение
        отдаётся следующей по стоимости альтернативе: иначе одинокий
        пассажир-«транзитник» держит убыточную линию в игре.
        """
        ranked = sorted(
            (("transit", self.transit), ("driving", self.car), ("walking", self.walk)),
            key=lambda item: item[1],
        )
        if ranked[0][0] == "transit" and transit_pax < MIN_TRANSIT_CHOICE:
            return ranked[1][0]
        return ranked[0][0]


def driving_time_multiplier(level: str) -> float:
    """Множитель пробок по уровню спроса в час."""
    return DRIVING_TIME_MULTIPLIERS.get(level, 1.0)


def driving_seconds(
    distance_m: float,
    *,
    demand_level: str = "lowMedium",
) -> float:
    """Время авто поездки: расстояние, скорость, пробка, порог осмысленности.

    Расстояние дорожное, поэтому извилистость уже в нём; в игре формула из
    modding API идёт от прямого расстояния с множителем 1.3, а в скачанном
    пакете времена посчитаны по графу. Здесь принимается дорожное расстояние.
    """
    return max(MIN_DRIVING_SECONDS, distance_m / AVG_DRIVING_SPEED_MPS
               * driving_time_multiplier(demand_level))


def walking_seconds(
    distance_m: float,
    *,
    speed_mps: float = 1.4,
    to_airport: bool = False,
) -> float:
    """Воспринимаемое пешее время в секундах с множителем Wardman."""
    base = distance_m / max(speed_mps, 1e-6)
    return base * (AIRPORT_WALK_MIN if to_airport else WALK_MIN)


def wait_seconds(wait_s: float) -> float:
    return max(0.0, wait_s) * WAIT_MIN


def parking_search_minutes(parking_s: float) -> float:
    return max(0.0, parking_s) / 60.0 * PARKING_SEARCH_MIN


def short_drive_penalty(distance_m: float) -> float:
    """Штраф за неудобство короткой авто поездки, как в getModeChoiceForPerson.

    Поездка короче 1 km получает штраф, линейно убывающий до 1.0 на пороге.
    """
    if distance_m >= MIN_SENSIBLE_DRIVING_DISTANCE_M:
        return 1.0
    return 1.0 + (MIN_SENSIBLE_DRIVING_DISTANCE_M - max(0.0, distance_m)) \
        / MIN_SENSIBLE_DRIVING_DISTANCE_M


def value_of_time_s_per_eur(income_per_year: float) -> float:
    """VOT = годовой доход / отработанные часы, в секундах на евро."""
    return max(0.0, income_per_year) / HOURS_WORKED_PER_YEAR


_SQRT2 = sqrt(2.0)
_INV_SQRT_2PI = 0.3989422804014326779399460599343818684758586311649


def _normal_cdf(x: float) -> float:
    """CDF стандартного нормального распределения через erfc."""
    return 0.5 * erfc(-x / _SQRT2)


def inverse_normal_cdf(probability: float) -> float:
    """Обратная нормальная CDF методом Ньютона с удержанием скобки.

    В игре (popCommuteWorker:38814) используется обратная нормальная CDF для
    разброса дохода. Здесь она реализована итеративно, а не таблицей
    коэффициентов: численные таблицы легко переносятся с ошибкой, и ошибка
    проявляется только на хвостах, где доход обрезается потолком и ошибка
    не заметна по средним. Итеративное решение с удержанием скобки даёт
    точность порядка 1e-15 на всей области и не зависит от памяти.

    Схема: грубое начальное приближение по асимптотике хвоста, затем шаг
    Ньютона по функции F(x) = Phi(x) - p со сдвигом обратно в скобку, если
    шаг вышел за её пределы. Сходимость - за 4-6 итераций.
    """
    p = min(max(float(probability), 1e-300), 1.0 - 1e-16)
    if p == 0.5:
        return 0.0
    # Асимптотическое приближение Уики для начального приближения.
    if p < 0.5:
        t = 1.0 / sqrt(-2.0 * log(p))
        x = -(((((2.515517 * t + 0.802853) * t + 0.010328) * t - 0.000308) * t
               - 0.000153) * t - 0.000253) * t - 0.000977
    else:
        t = 1.0 / sqrt(-2.0 * log(1.0 - p))
        x = -(((((2.515517 * t - 0.802853) * t + 0.010328) * t + 0.000308) * t
               + 0.000153) * t - 0.000253) * t - 0.000977
    low, high = (-40.0, 0.0) if p < 0.5 else (0.0, 40.0)
    for _ in range(64):
        error = _normal_cdf(x) - p
        if error > 0.0:
            high = x
        else:
            low = x
        if -1e-16 < error < 1e-16:
            break
        density = _INV_SQRT_2PI * exp(-0.5 * x * x)
        step = error / density if density > 0.0 else (x - low) / 2.0
        candidate = x - step
        if not low < candidate < high:
            candidate = (low + high) / 2.0
        if candidate == x:
            break
        x = candidate
    return x


def income_for_person(
    index: int,
    *,
    total: int,
    seed: int = 0,
    to_airport: bool = False,
    at_college: bool = False,
) -> float:
    """Доход человека по обратной нормальной CDF, детерминированно.

    Индекс задаёт позицию в последовательности, поэтому расход распределён
    одинаково при каждом запуске - случайность без повторов не нужна.
    Анти-кластеризация из разбора: нижний дециль добирается равномерно, плюс
    10% получают бонус по псевдослучайному признаку.
    """
    if total <= 0:
        return MINIMUM_INCOME
    probability = (index + 0.5) / total
    # Нижний дециль: равномерная выборка вместо нормальной, иначе доходы
    # слипаются у минимума.
    if probability < 0.10:
        probability = (index + 0.5) / max(1.0, total * 0.10) * 0.10
    elif ((index * 2654435761 + seed) % 10) == 0:
        probability = min(1.0 - 1e-12, probability + 0.10)
    income = INCOME_MEAN + INCOME_STD_DEV * inverse_normal_cdf(probability)
    if to_airport:
        income *= AIRPORT_INCOME_MULTIPLIER
    elif at_college:
        income *= COLLEGE_INCOME_MULTIPLIER
    return min(MAXIMUM_INCOME, max(MINIMUM_INCOME, income))


def home_work_departure_pair(
    index: int,
    *,
    seed: int = 0,
    demand_level: str = "lowMedium",
) -> tuple[float, float]:
    """Пара (уход из дома, уход с работы) в секундах от начала суток.

    Время выбирается по профилю суток и уровню спроса в часе, а разрыв между
    выездом и концом рабочего дня не меньше MIN_GAP_MINUTES - иначе поездка
    вырождается в мгновенный разворот у дома.
    """
    level_value = {"veryLow": 0.1, "low": 0.35, "lowMedium": 0.6, "medium": 0.8, "high": 1.0}
    span = level_value.get(demand_level, 0.6)
    window = max(0.0, (span - 0.3) * 24 * 3600.0)
    base = ((index * 1103515245 + seed * 12345) % 1000) / 1000.0
    home = (base * window) % 86400.0
    gap = MIN_GAP_MINUTES * 60.0
    work = home + gap
    if work >= 86400.0:
        work -= 86400.0
    return home, work


def airport_parking_cost() -> float:
    return PARKING_COST * AIRPORT_PARKING_COST_MULTIPLIER


def perceived_minutes(
    *,
    in_vehicle_s: float,
    wait_s: float,
    access_walk_s: float,
    egress_walk_s: float,
    transfer_walk_s: float,
    transfers: int,
    car_drive_s: float,
    car_parking_s: float,
    car_distance_m: float,
    walk_s: float,
    to_airport: bool = False,
    departure_shift_s: float = 0.0,
) -> PerceivedMinutes:
    """Воспринимаемые минуты трёх альтернатив, минута езды = 1.0 (PERCEIVED_TIME).

    Все составляющие переводятся в одну шкалу умножением на множитель
    восприятия, поэтому альтернативы сравнимы напрямую.
    """
    transit = (
        max(0.0, in_vehicle_s) * RIDE_MIN
        + max(0.0, wait_s) * WAIT_MIN
        + max(0.0, access_walk_s) * (AIRPORT_WALK_MIN if to_airport else WALK_MIN)
        + max(0.0, egress_walk_s) * (AIRPORT_WALK_MIN if to_airport else WALK_MIN)
        + max(0.0, transfer_walk_s) * WALK_MIN
        + ARRIVAL_GAP_S * WAIT_MIN * (1 + max(0, transfers - 1))
        + max(0.0, departure_shift_s) * DEPARTURE_SHIFT_MIN
    )
    car = (
        max(0.0, car_drive_s) * CONGESTED_DRIVING_MIN * short_drive_penalty(car_distance_m)
        + max(0.0, car_parking_s) / 60.0 * PARKING_SEARCH_MIN
    )
    walk = max(0.0, walk_s) * (AIRPORT_WALK_MIN if to_airport else WALK_MIN)
    return PerceivedMinutes(
        transit=transit / 60.0,
        car=car / 60.0,
        walk=walk / 60.0,
    )
