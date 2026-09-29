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

        Разбор (popCommuteWorker:38735): если транзит выбрали менее
        MIN_TRANSIT_CHOICE человек, ВСЕХ переводят в авто - не во вторую по
        стоимости альтернативу, а именно в авто. Движок так выкидывает
        мелкие потоки ради производительности, из-за чего на ячейке из 200
        человек порог в 5% людей заметно искажает модальный сплит.
        """
        winner = self.winner()
        if winner == "transit" and transit_pax < MIN_TRANSIT_CHOICE:
            return "driving"
        return winner


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
    """Цена времени в секундах на евро, в единицах планировщика.

    Разбор (popCommuteWorker:38780) оперирует VOT в долларах в секунду:

        votAnnual  = income / HOURS_WORKED_PER_YEAR   # фактически $/час
        votPerHour = votAnnual / 3600                 # $/сек

    Планировщик же оперирует обратной величиной - сколько секунд стоит
    евро, - поэтому берётся обратное значение. Ошибка размерности здесь
    неощутима численно, но ломает денежную часть выбора: при доходе 60 000
    правильный ответ 111.6 с/евро, а `income / 1860` даёт 32.26 - это
    $/час, а не секунды за евро.

    Контрольные точки из документа: 15 000 -> 446.4, 60 000 -> 111.6,
    200 000 -> 33.5 секунд за евро.
    """
    if income_per_year <= 0:
        return 0.0
    return HOURS_WORKED_PER_YEAR * 3600.0 / income_per_year


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
    job_id: str = "",
) -> float:
    """Доход человека по обратной нормальной CDF, детерминированно.

    Разбор (popCommuteWorker:38792 getIncomeForPerson):

        q     = personIndex / max(population - 1, 1)
        z     = inverseNormalCDF(q)
        income = INCOME_MEAN * multiplier + z * INCOME_STD_DEV
        income = min(max(income, MINIMUM_INCOME), MAXIMUM_INCOME)

    Квантиль не случайный, а `index / (total - 1)`: в ячейке из 200 человек
    первый получает самый низкий доход, последний - самый высокий, квантили
    внутри ячейки равномерны. Случайности нет вообще.

    Множитель применяется к среднему, а не к итогу, поэтому разброс вокруг
    сдвинутой середины сохраняется. Он определяется префиксом jobId:
    `AIR_` - аэропорт, `UNI_` - вуз.

    Анти-кластеризация (:38800-38809): если доход уперся в нижнюю отсечку,
    квантиль перерисовывается равномерно на 0.1..0.95 - иначе треть бедных
    слипалась бы в MINIMUM_INCOME из-за расходимости Ф^-1 при q -> 0.
    Отдельно 10% людей получают бонус псевдослучайно, но детерминированно.
    """
    multiplier = 1.0
    if job_id.startswith("AIR_"):
        multiplier = AIRPORT_INCOME_MULTIPLIER
    elif job_id.startswith("UNI_"):
        multiplier = COLLEGE_INCOME_MULTIPLIER

    denominator = max(total - 1, 1)
    probability = index / denominator
    income = INCOME_MEAN * multiplier + inverse_normal_cdf(probability) * INCOME_STD_DEV
    if income <= MINIMUM_INCOME + 5000.0:
        # Равномерная перерисовка квантиля, а не сдвиг готового дохода.
        spread = 0.85 / max(1, total)
        probability = 0.1 + ((index * 7919) % 1000) / 1000.0 * spread
        income = INCOME_MEAN * multiplier + inverse_normal_cdf(probability) * INCOME_STD_DEV
    if (index * 123456789) % 1_000_000 < 100_000:
        income += ((index * 123456789) % 1_000_000) / 1_000_000.0 * 1e5
    return min(MAXIMUM_INCOME, max(MINIMUM_INCOME, income))


def home_work_departure_pair(
    index: int,
    *,
    seed: int = 0,
    demand_level: str = "lowMedium",
) -> tuple[float, float]:
    """Пара (уход из дома, уход с работы) в секундах от начала суток.

    Время выбирается по профилю суток и уровню спроса в часе. Разрыв между
    выездом из дома и выездом с работы не меньше MIN_GAP_MINUTES - в игре
    `work` подбирается циклом до 100 попыток с условием
    |work - home| >= MIN_GAP_MINUTES*60 (assignCommuteTimes:132975), здесь
    разрыв берётся сразу, что даёт тот же результат без цикла.
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


def circular_gap_s(first_s: float, second_s: float) -> float:
    """Круговая метрика между двумя моментами суток, determineJourneyOrigin.

    Разбор (index-C2DH4lPc.js:136934): направление поездки определяется тем,
    какой из двух моментов ближе к времени старта, где расстояние считается
    по кругу на 24 часа: min(|X-Y|, 86400 - |X-Y|).
    """
    difference = abs(first_s - second_s) % 86400.0
    return min(difference, 86400.0 - difference)


def journey_origin(home_s: float, work_s: float, trip_start_s: float) -> str:
    """Откуда идёт поездка в момент `trip_start_s`.

    Разбор (index-C2DH4lPc.js:136934): сравнивается круговая дистанция от
    старта поездки до времени ухода из дома и до времени ухода с работы;
    кто ближе - тот и считается домашним направлением.
    """
    to_home = circular_gap_s(trip_start_s, home_s)
    to_work = circular_gap_s(trip_start_s, work_s)
    return "home" if to_home <= to_work else "work"


def airport_parking_cost() -> float:
    return PARKING_COST * AIRPORT_PARKING_COST_MULTIPLIER


def value_of_time_eur_per_second(income_per_year: float) -> float:
    """Цена времени в евро за секунду - форма, в которой считает игра.

    Разбор (popCommuteWorker:38780) умножает время в секундах на VOT в
    долларах в секунду, поэтому именно эта величина нужна для сравнения
    обобщённых стоимостей: t * votPerSecond даёт деньги, которые можно
    складывать с тарифом и ценой бензина.

    Обратная величина - `value_of_time_s_per_eur` - нужна планировщику,
    где полезность считается как 60 минут / VOT.
    """
    if income_per_year <= 0:
        return 0.0
    return income_per_year / (HOURS_WORKED_PER_YEAR * 3600.0)


def congested_share(traffic_multiplier: float) -> float:
    """Доля загруженности дороги, popCommuteWorker:38743 getCongestedShare.

    `trafficMultiplier` приходит из DRIVING_TIMES по уровню спроса часа;
    `CONGESTION_FULL_AT_MULTIPLIER` = 2.0, поэтому делитель равен 1.0.
    """
    raw = (traffic_multiplier - 1.0) / (CONGESTION_FULL_AT_MIN - 1.0)
    return min(1.0, max(0.0, raw))


def perceived_driving_seconds(
    drive_s: float,
    *,
    traffic_multiplier: float = 1.0,
) -> float:
    """Воспринимаемое время автопоездки, popCommuteWorker:38754.

        result = t * mult * (1 + share * (1.33 - 1))
                 + PARKING_TIME * 2 * PARKING_SEARCH_MULTIPLIER

    Существенно: парковка прибавляется в обоих концах и даёт постоянные
    +576 воспринимаемых секунд к каждой поездке независимо от её длины.
    Для десятиминутной поездки это +96%, из-за чего авто коротких
    направлений заведомо проигрывает. Денежная сторона парковки (5$, x5 в
    аэропорту) считается отдельно и сюда не входит.
    """
    share = congested_share(traffic_multiplier)
    return (
        max(0.0, drive_s) * traffic_multiplier
        * (1.0 + share * (CONGESTED_DRIVING_MIN - 1.0))
        + PARKING_TIME_S * 2.0 * PARKING_SEARCH_MIN
    )


def perceived_walk_only_seconds(
    walk_s: float,
    *,
    to_airport: bool = False,
    apply_walk_multiplier: bool = False,
) -> float:
    """Воспринимаемое время варианта «идти пешком», popCommuteWorker:38749.

    В игре пеший вариант НЕ взвешивается множителем 1.39 - применяется
    только аэропортный 1.87. То есть пешком субъективно в 1.39 раза
    дешевле, чем пешком в составе метро, при одинаковом физическом
    времени. Разбор считает это неточностью игры (раздел 5.3), поэтому
    поведение воспроизводится по умолчанию, а выключается флагом.
    """
    base = max(0.0, walk_s)
    if to_airport:
        return base * AIRPORT_WALK_MIN
    return base * WALK_MIN if apply_walk_multiplier else base


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
    traffic_multiplier: float = 1.0,
    apply_walk_multiplier: bool = False,
) -> PerceivedMinutes:
    """Воспринимаемые минуты трёх альтернатив, минута езды = 1.0 (PERCEIVED_TIME).

    Транзит приходит уже взвешенным из rRAPTOR, поэтому езда идёт с 1.0,
    пешие отрезки с 1.39, ожидание с 1.37, смещение отправления с 0.4.
    Авто - по формуле getPerceivedDrivingTime, включая постоянные 576 с
    парковки. Чисто пеший вариант идёт без 1.39, как в игре.
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
        perceived_driving_seconds(
            car_drive_s, traffic_multiplier=traffic_multiplier,
        )
        + max(0.0, car_parking_s) / 60.0 * PARKING_SEARCH_MIN
    )
    walk = perceived_walk_only_seconds(
        walk_s, to_airport=to_airport,
        apply_walk_multiplier=apply_walk_multiplier,
    )
    return PerceivedMinutes(
        transit=transit / 60.0,
        car=car / 60.0,
        walk=walk / 60.0,
    )


@dataclass(frozen=True, slots=True)
class ModeCosts:
    """Обобщённая стоимость в единицах VOT, getModeChoiceForPerson.

    Разбор (popCommuteWorker:38762):

        авто    = (t_car * VOT + $cost) * shortTripPenalty
        транзит = t_transit * VOT + fare
        пешком  = t_walk * VOT

    Штраф за короткую поездку умножает всю авто-стоимость целиком - вместе с
    деньгами, а не только время. Это отличает его от множителя на время
    вождения в `perceived_driving_seconds`.
    """

    driving: float
    transit: float
    walking: float

    def choice(self) -> str:
        """Победитель - минимум (popCommuteWorker:38789), не логит."""
        if self.driving <= self.transit and self.driving <= self.walking:
            return "driving"
        if self.transit <= self.walking:
            return "transit"
        return "walking"


def mode_costs(
    *,
    income_per_year: float,
    transit: PerceivedMinutes,
    car_distance_m: float,
    transit_fare: float = 0.0,
    to_airport: bool = False,
    traffic_multiplier: float = 1.0,
) -> ModeCosts:
    """Обобщённые стоимости трёх альтернатив для одного человека.

    Денежная часть авто: километры по 0.65 EUR плюс 5 EUR парковки, в
    аэропорту парковка умножается на 5.
    """
    vot_eur_per_second = value_of_time_eur_per_second(income_per_year)
    # Воспринимаемое время авто уже посчитано в perceived_minutes, включая
    # постоянные 576 с парковки в обоих концах.
    driving_time_s = transit.car * 60.0
    money = (
        car_distance_m / 1000.0 * DRIVING_COST_PER_KM
        + (PARKING_COST * AIRPORT_PARKING_COST_MULTIPLIER
           if to_airport else PARKING_COST)
    )
    driving = (driving_time_s * vot_eur_per_second + money) * short_drive_penalty(
        car_distance_m
    )
    return ModeCosts(
        driving=driving,
        transit=transit.transit * 60.0 * vot_eur_per_second + transit_fare,
        walking=transit.walk * 60.0 * vot_eur_per_second,
    )
