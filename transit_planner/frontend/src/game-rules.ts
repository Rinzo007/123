/**
 * Правила пути и стоимости поездки по разбору Subway Builder 1.6.0.
 *
 * Port of `src/transit_planner/game_rules.py`. Константы взяты из
 * PATHFINDING_RULES_SPEC и PERCEIVED_TIME, то есть из поставляемой сборки,
 * а не подогнаны под правдоподобие. Множители приведены к минуте езды,
 * равной 1.0; в `choice.ts` шкала другая, абсолютная, поэтому наборы
 * не подменяют друг друга.
 */

/** Множитель пешего времени, Wardman: минута езды = 1.0. */
export const GAME_WALK_MIN = 1.39;
/** С багажом и в аэропорту пешее время воспринимается тяжелее. */
export const GAME_AIRPORT_WALK_MIN = 1.87;
/** Ожидание. */
export const GAME_WAIT_MIN = 1.37;
/** Смещение отправления. */
export const GAME_DEPARTURE_SHIFT_MIN = 0.4;
/** Вождение в заторе. */
export const GAME_CONGESTED_DRIVING_MIN = 1.33;
/** Поиск парковки. */
export const GAME_PARKING_SEARCH_MIN = 1.6;

export const GAME_MAX_TRANSFERS = 4;
export const GAME_MAX_WALK_TO_FROM_STATION_S = 45 * 60;
export const GAME_MAX_DRIVE_TO_FROM_STATION_S = 7 * 60;
export const GAME_MAX_TRANSFER_WALKING_TIME_S = 10 * 60;
export const GAME_ARRIVAL_GAP_S = 50;
export const GAME_DRIVING_COST_PER_KM = 0.65;
export const GAME_MIN_SENSIBLE_DRIVING_DISTANCE_M = 1000;
export const GAME_PARKING_TIME_S = 180;
export const GAME_PARKING_COST = 5.0;
export const GAME_MIN_TRANSIT_CHOICE = 10;

/** Окно отправлений rRAPTOR: 30 минут (PATHFINDING_RULES.RANGE_QUERY_WINDOW). */
export const GAME_RANGE_QUERY_WINDOW_MIN = 30;
/** Потолок отправлений на запрос: без него 30-минутное окно даёт 31 прогон. */
export const GAME_MAX_RANGE_DEPARTURES = 24;
/** Предрасчёт рейсов вперёд: 2 часа (FUTURE_CYCLE_TIME_OFFSET). */
export const GAME_FUTURE_CYCLE_TIME_OFFSET_MIN = 120;
/** Минимум предрассчитанных циклов расписания (MIN_FUTURE_CYCLES). */
export const GAME_MIN_FUTURE_CYCLES = 2;
/** Множитель времени в пути на единицу (RIDE_MIN): езда не утяжеляется. */
export const GAME_RIDE_MIN = 1.0;
/** Извилистость дорожного расстояния относительно прямого (DRIVING). */
export const GAME_CAR_CIRCUITY = 1.3;

export const GAME_HOURS_WORKED_PER_YEAR = 1860;
export const GAME_MINIMUM_INCOME = 15000;
export const GAME_MAXIMUM_INCOME = 200000;
export const GAME_AVG_DRIVING_SPEED_MPS = 11.1;
/**
 * Скорость ходьбы по дорожному пути в игре: 1.5 м/с, то есть ровно
 * 5.4 км/ч. Домашний (прямолинейный) режим игры быстрее, но парсер
 * отдаёт 1.5 м/с.
 */
export const GAME_WALKING_SPEED_MPS = 1.5;
export const GAME_WALKING_SPEED_KPH = 1.5 * 3.6;
export const GAME_MIN_DRIVING_SECONDS = 60;
export const GAME_MIN_GAP_MINUTES = 90;

/** Множитель времени вождения по уровню спроса в час. */
export const GAME_DRIVING_TIME_MULTIPLIERS: Readonly<Record<string, number>> = {
  veryLow: 0.8,
  low: 0.9,
  lowMedium: 1.0,
  medium: 1.25,
  high: 1.5,
};

/** Штраф за неудобство короткой авто поездки: линейно убывает до 1.0. */
export function shortDrivePenalty(distanceM: number): number {
  if (distanceM >= GAME_MIN_SENSIBLE_DRIVING_DISTANCE_M) return 1.0;
  return 1.0
    + (GAME_MIN_SENSIBLE_DRIVING_DISTANCE_M - Math.max(0, distanceM))
    / GAME_MIN_SENSIBLE_DRIVING_DISTANCE_M;
}

/** VOT = годовой доход / отработанные часы, в секундах на евро. */
export function valueOfTimeSPerEur(incomePerYear: number): number {
  return Math.max(0, incomePerYear) / GAME_HOURS_WORKED_PER_YEAR;
}

export interface PerceivedMinutes {
  transit: number;
  car: number;
  walk: number;
}

/** Выбор с правилом MIN_TRANSIT_CHOICE: одинокий пассажир уступает. */
export function preferredMode(
  costs: PerceivedMinutes,
  transitPax = 0,
): "transit" | "driving" | "walking" {
  const ranked: [("transit" | "driving" | "walking"), number][] = [
    ["transit", costs.transit],
    ["driving", costs.car],
    ["walking", costs.walk],
  ];
  ranked.sort((a, b) => a[1] - b[1]);
  if (ranked[0][0] === "transit" && transitPax < GAME_MIN_TRANSIT_CHOICE) {
    return "driving";
  }
  return ranked[0][0];
}

export const GAME_INCOME_MEAN = 60000;
export const GAME_INCOME_STD_DEV = 25000;
export const GAME_INCOME_LADDER_SIZE = 200;
export const GAME_CAR_COST_PER_KM = GAME_DRIVING_COST_PER_KM;
export const GAME_AIRPORT_PARKING_MULTIPLIER = 5;
export const GAME_AIRPORT_INCOME_MULTIPLIER = 1.5;
export const GAME_COLLEGE_INCOME_MULTIPLIER = 0.6;
/** Синхронизировано с DRIVING_TIMES.HIGH_DEMAND. */
export const GAME_CONGESTION_FULL_AT = 2.0;

/** Цена времени в долларах за секунду - форма, в которой считает игра. */
export function valueOfTimeEurPerSecond(incomePerYear: number): number {
  if (incomePerYear <= 0) return 0;
  return incomePerYear / (GAME_HOURS_WORKED_PER_YEAR * 3600);
}

/**
 * Обратная нормальная CDF методом Ньютона с удержанием скобки.
 *
 * Таблица коэффициентов не переносится: ошибка в ней проявляется только
 * на хвостах, где доход упирается в потолок, и по средним незаметна.
 */
/**
 * erfc с точностью порядка 1e-15, чтобы совпадать с `math.erfc` в Python.
 *
 * Приближение Абрамовича-Стигана даёт лишь 1.5e-7, и этой точности не
 * хватает: обратная CDF смещается, и на границах выбора режима несколько
 * человек переворачиваются, а паритет Python/TS требует совпадения.
 *
 * Для малых аргументов берётся ряд Тейлора erf, для остальных - непрерывная
 * дробь Лаплаца, которая сходится машинно точно.
 *
 * Порог и число членов подобраны так, чтобы обе ветви давали ошибку не
 * больше 1e-16: ряд Тейлора точен до |x| = 1.5 (дальше начинается
 * взаимное сокращение членов), а обратная дробь Лапласа на 1.5 выходит уже
 * на машинную точность.
 */
const ERFC_LAPLACE_SWITCH = 1.5;
const ERFC_LAPLACE_TERMS = 120;

function erfSmall(x: number): number {
  // erf(x) = (2/sqrt(pi)) * sum (-1)^n x^(2n+1) / (n! (2n+1))
  let term = x;
  let sum = x;
  for (let n = 1; n < 60; n += 1) {
    term *= -x * x / n;
    sum += term / (2 * n + 1);
    if (Math.abs(term / (2 * n + 1)) < 1e-18 * Math.abs(sum)) break;
  }
  return 2 / Math.sqrt(Math.PI) * sum;
}

function erfcContinuedFraction(x: number): number {
  // erfc(x) = exp(-x^2)/sqrt(pi) * 1/(x + (1/2)/(x + 1/(x + (3/2)/(x + ...))))
  //
  // Дробь Лапласа считается с конца, а не по Ленцу: у нуля прямая
  // рекурсия diverges, обратная же сходится и при x = 1.5 даёт точность
  // 0 ulp, а ряд Тейлора на том же пороге - 1e-16.
  let u = x;
  for (let n = ERFC_LAPLACE_TERMS - 1; n >= 1; n -= 1) {
    u = x + (n * 0.5) / u;
  }
  return Math.exp(-x * x) / Math.sqrt(Math.PI) / u;
}

function erfCx(x: number): number {
  const sign = x < 0 ? -1 : 1;
  const a = Math.abs(x);
  const value = a < ERFC_LAPLACE_SWITCH ? 1 - erfSmall(a) : erfcContinuedFraction(a);
  return sign > 0 ? value : 2 - value;
}

function normalCdf(x: number): number {
  return 0.5 * erfCx(-x / Math.SQRT2);
}

export function inverseNormalCdf(probability: number): number {
  const p = Math.min(Math.max(probability, 1e-300), 1 - 1e-16);
  if (p === 0.5) return 0;
  let t: number;
  let x: number;
  if (p < 0.5) {
    t = 1 / Math.sqrt(-2 * Math.log(p));
    x = -(((((2.515517 * t + 0.802853) * t + 0.010328) * t - 0.000308) * t
      - 0.000153) * t - 0.000253) * t - 0.000977;
  } else {
    t = 1 / Math.sqrt(-2 * Math.log(1 - p));
    x = -(((((2.515517 * t - 0.802853) * t + 0.010328) * t + 0.000308) * t
      + 0.000153) * t - 0.000253) * t - 0.000977;
  }
  let low = -40;
  let high = 0;
  if (p > 0.5) {
    low = 0;
    high = 40;
  }
  for (let i = 0; i < 64; i += 1) {
    const error = normalCdf(x) - p;
    if (error > 0) high = x;
    else low = x;
    if (error > -1e-16 && error < 1e-16) break;
    const density = 0.39894228040143267793994605993438 * Math.exp(-0.5 * x * x);
    const step = density > 0 ? error / density : (x - low) / 2;
    let candidate = x - step;
    if (!(candidate > low && candidate < high)) candidate = (low + high) / 2;
    // Сравнение со СТАРЫМ x: иначе условие всегда истинно и выполняется
    // ровно один шаг Ньютона вместо сходимости.
    if (candidate === x) break;
    x = candidate;
  }
  return x;
}

/** Доход человека: квантиль index/(total-1), множитель на среднее. */
export function incomeForPerson(index: number, total: number, jobId = ""): number {
  let multiplier = 1;
  if (jobId.startsWith("AIR_")) multiplier = GAME_AIRPORT_INCOME_MULTIPLIER;
  else if (jobId.startsWith("UNI_")) multiplier = GAME_COLLEGE_INCOME_MULTIPLIER;
  const denominator = Math.max(total - 1, 1);
  const probability = index / denominator;
  let income = GAME_INCOME_MEAN * multiplier
    + inverseNormalCdf(probability) * GAME_INCOME_STD_DEV;
  income = Math.min(Math.max(income, GAME_MINIMUM_INCOME), GAME_MAXIMUM_INCOME);
  // Псевдослучайное u из индекса: в игре (index * 362436069) % 1e6 / 1e6.
  const noise = (index * 362436069) % 1000000 / 1000000;
  if (income <= GAME_MINIMUM_INCOME + 5000) {
    // Перерисовка квантиля на 0.1..0.95: без неё Ф^-1(q) при q -> 0 уводит
    // нижнюю треть в отсечку и бедные слипаются в одно значение.
    income = GAME_INCOME_MEAN * multiplier
      + inverseNormalCdf(0.1 + noise * 0.85) * GAME_INCOME_STD_DEV;
  } else if (noise < 0.1) {
    // Бонус достаётся не тем u, что перерисовка, и только не бедным.
    const mod = (index * 123456789) % 1000000;
    income += (mod / 1000000) * 1e5;
  }
  return income;
}

/** Воспринимаемое время авто: +180*2*1.6 = 576 с к каждой поездке. */
export function perceivedDrivingSeconds(
  driveS: number,
  trafficMultiplier = 1,
): number {
  const share = Math.min(1, Math.max(0, (trafficMultiplier - 1) / (GAME_CONGESTION_FULL_AT - 1)));
  return Math.max(0, driveS) * trafficMultiplier
    * (1 + share * (GAME_CONGESTED_DRIVING_MIN - 1))
    + GAME_PARKING_TIME_S * 2 * GAME_PARKING_SEARCH_MIN;
}

/**
 * Чисто пеший вариант: в игре НЕ взвешивается 1.39, применяется только
 * аэропортный 1.87. Флага такого в игре нет - реестр фич-флагов полный.
 * Поведение игры воспроизведено; `applyWalkMultiplier` оставлен для сверки.
 */
export function perceivedWalkOnlySeconds(
  walkS: number,
  toAirport = false,
  applyWalkMultiplier = false,
): number {
  const base = Math.max(0, walkS);
  if (toAirport) return base * GAME_AIRPORT_WALK_MIN;
  return applyWalkMultiplier ? base * GAME_WALK_MIN : base;
}

export interface ModeCosts {
  driving: number;
  transit: number;
  walking: number;
}

/** Обобщённые стоимости трёх альтернатив в единицах VOT. */
export function modeCosts(
  incomePerYear: number,
  transitPerceivedSec: number,
  carPerceivedSec: number,
  walkPerceivedSec: number,
  carDistanceM: number,
  transitFare: number,
  toAirport = false,
): ModeCosts {
  const vot = valueOfTimeEurPerSecond(incomePerYear);
  const parking = toAirport
    ? GAME_PARKING_COST * GAME_AIRPORT_PARKING_MULTIPLIER
    : GAME_PARKING_COST;
  const money = carDistanceM / 1000 * GAME_CAR_COST_PER_KM + parking;
  return {
    driving: (carPerceivedSec * vot + money) * shortDrivePenalty(carDistanceM),
    transit: transitPerceivedSec * vot + transitFare,
    walking: walkPerceivedSec * vot,
  };
}

export interface GameSplitInput {
  transitPerceivedSec: number;
  carPerceivedSec: number;
  walkPerceivedSec: number;
  carDistanceM: number;
  transitFare: number;
  toAirport?: boolean;
  transitAvailable?: boolean;
  population?: number;
  applyMinTransitChoice?: boolean;
  applyWalkMultiplier?: boolean;
}

export type GameModeShares = {
  transit: number; car: number; walk: number;
};

/**
 * Модальный сплит по правилу игры: минимум обобщённой стоимости,
 * интегрированный по детерминированной лестнице дохода.
 */
export function gameModeSplit(input: GameSplitInput): GameModeShares {
  const population = input.population ?? GAME_INCOME_LADDER_SIZE;
  const counts = { transit: 0, car: 0, walk: 0 };
  if (population <= 0) return counts;
  for (let index = 0; index < population; index += 1) {
    const income = incomeForPerson(index, population);
    const costs = modeCosts(
      income,
      input.transitPerceivedSec,
      input.carPerceivedSec,
      input.walkPerceivedSec,
      input.carDistanceM,
      input.transitFare,
      input.toAirport,
    );
    // Приоритет при равенстве: авто, потом транзит, иначе пешком.
    const candidates: [keyof GameModeShares, number][] = [["car", costs.driving]];
    if (input.transitAvailable !== false) candidates.push(["transit", costs.transit]);
    candidates.push(["walk", costs.walking]);
    let bestKey: keyof GameModeShares = candidates[0][0];
    let best = candidates[0][1];
    for (const [key, cost] of candidates) {
      if (cost < best) { best = cost; bestKey = key; }
    }
    counts[bestKey] += 1;
  }
  if (input.applyMinTransitChoice !== false && counts.transit < GAME_MIN_TRANSIT_CHOICE) {
    counts.car += counts.transit;
    counts.transit = 0;
  }
  const total = counts.transit + counts.car + counts.walk;
  if (total <= 0) return { transit: 0, car: 0, walk: 0 };
  return {
    transit: counts.transit / total,
    car: counts.car / total,
    walk: counts.walk / total,
  };
}
