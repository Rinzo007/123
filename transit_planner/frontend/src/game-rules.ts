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

export const GAME_HOURS_WORKED_PER_YEAR = 1860;
export const GAME_MINIMUM_INCOME = 15000;
export const GAME_MAXIMUM_INCOME = 200000;
export const GAME_AVG_DRIVING_SPEED_MPS = 11.1;
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
 */
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
  // Lentz для устойчивой оценки непрерывной дроби.
  const tiny = 1e-300;
  let f = tiny;
  let c = f;
  let d = 0;
  for (let i = 0; i < 300; i += 1) {
    const a = i === 0 ? 1 : i * 0.5;
    d = x + a;
    if (Math.abs(d) < tiny) d = tiny;
    c = f + a / d;
    if (Math.abs(c) < tiny) c = tiny;
    d = 1 / c;
    const delta = c * d;
    f *= delta;
    if (Math.abs(delta - 1) < 1e-17) break;
  }
  return Math.exp(-x * x) / Math.sqrt(Math.PI) * f;
}

function erfCx(x: number): number {
  const sign = x < 0 ? -1 : 1;
  const a = Math.abs(x);
  const value = a < 0.6 ? 1 - erfSmall(a) : erfcContinuedFraction(a);
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
  let probability = index / denominator;
  let income = GAME_INCOME_MEAN * multiplier
    + inverseNormalCdf(probability) * GAME_INCOME_STD_DEV;
  if (income <= GAME_MINIMUM_INCOME + 5000) {
    const spread = 0.85 / Math.max(1, total);
    probability = 0.1 + ((index * 7919) % 1000) / 1000 * spread;
    income = GAME_INCOME_MEAN * multiplier
      + inverseNormalCdf(probability) * GAME_INCOME_STD_DEV;
  }
  const mod = (index * 123456789) % 1000000;
  if (mod < 100000) income += (mod / 1000000) * 1e5;
  return Math.min(GAME_MAXIMUM_INCOME, Math.max(GAME_MINIMUM_INCOME, income));
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

/** Чисто пеший вариант: по умолчанию 1.39, в игре без него. */
export function perceivedWalkOnlySeconds(
  walkS: number,
  toAirport = false,
  applyWalkMultiplier = true,
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
  carAvailability?: number;
  restTimeMin?: number | null;
  population?: number;
  applyMinTransitChoice?: boolean;
  applyWalkMultiplier?: boolean;
}

export type GameModeShares = {
  transit: number; car: number; walk: number; rest: number;
};

/**
 * Модальный сплит по правилу игры: минимум обобщённой стоимости,
 * интегрированный по детерминированной лестнице дохода.
 */
export function gameModeSplit(input: GameSplitInput): GameModeShares {
  const population = input.population ?? GAME_INCOME_LADDER_SIZE;
  const counts = { transit: 0, car: 0, walk: 0, rest: 0 };
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
    if (input.restTimeMin !== undefined && input.restTimeMin !== null) {
      candidates.push(["rest", Math.max(0, input.restTimeMin) * 60
        * valueOfTimeEurPerSecond(income)]);
    }
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
  const carAvailability = input.carAvailability ?? 1;
  if (carAvailability < 1) {
    const before = counts.car;
    counts.car = before * carAvailability;
    const displaced = before - counts.car;
    const active = counts.transit + counts.walk + counts.rest;
    if (active > 0) {
      counts.transit += displaced * counts.transit / active;
      counts.walk += displaced * counts.walk / active;
      counts.rest += displaced * counts.rest / active;
    } else {
      counts.rest += displaced;
    }
  }
  const total = counts.transit + counts.car + counts.walk + counts.rest;
  if (total <= 0) return { transit: 0, car: 0, walk: 0, rest: 0 };
  return {
    transit: counts.transit / total,
    car: counts.car / total,
    walk: counts.walk / total,
    rest: counts.rest / total,
  };
}
