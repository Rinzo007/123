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
    return ranked[1][0];
  }
  return ranked[0][0];
}
