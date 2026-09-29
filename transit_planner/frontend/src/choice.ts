/**
 * Mode choice: utilities, multinomial logit probabilities and the transit
 * alternative split.
 *
 * Port of `src/transit_planner/choice.py`. Walking-stage and transfer burdens
 * follow Ha, Lee & Ko (2020); walk/wait multipliers follow Wardman et al.
 * (2026). Literals mirror `model.json` and are asserted against it by
 * `tests/test_choice_parity.py`, so a model change cannot silently diverge.
 *
 * Short-drive penalty and the per-transfer arrival gap come from the
 * Subway Builder parse in `game-rules.ts`, which is the port of
 * `game_rules.py`.
 */

import {
  GAME_ARRIVAL_GAP_S,
  GAME_INCOME_LADDER_SIZE,
  GAME_MIN_SENSIBLE_DRIVING_DISTANCE_M,
  GAME_WALK_MIN,
  GAME_WAIT_MIN,
  gameModeSplit,
  perceivedDrivingSeconds,
  perceivedWalkOnlySeconds,
  shortDrivePenalty,
} from "./game-rules.js";

/** Value of time: `model.json` → `vot_s_per_eur`. */
export const REFERENCE_VOT_S_PER_EUR = 360;
/** `model.json` → `no_car_effectiveness`. */
export const REFERENCE_NO_CAR_EFFECTIVENESS = 0.78;
/** `model.json` → `journey_choice`.wait_weight (Wardman Table 5 mean). */
export const REFERENCE_WAIT_WEIGHT = 1.72;
/** `model.json` → `transfer`.rider_bias_s, converted to minutes. */
export const REFERENCE_TRANSFER_RIDER_BIAS_S = 0;
/** `model.json` → `transfer`.walk_multiplier, inside the walk circuity. */
export const REFERENCE_TRANSFER_WALK_MULTIPLIER = 1;
/** `model.json` → `transit_burdens`. */
export const REFERENCE_TRANSIT_STAGE_ACCESS_WEIGHT = 1.65;
export const REFERENCE_TRANSIT_STAGE_EGRESS_WEIGHT = 6.723;
export const REFERENCE_TRANSIT_STAGE_TRANSFER_WALK_WEIGHT = 9.906;
export const REFERENCE_TRANSIT_BURDEN_FIRST_MIN = 1.724;
export const REFERENCE_TRANSIT_BURDEN_MULTIPLE_MIN = 8.165;
/** `model.json` → `car`. */
export const REFERENCE_CAR_COST_PER_KM_EUR = 0.25;
export const REFERENCE_CAR_PARKING_EUR = 1.5;
export const REFERENCE_CAR_PARKING_S = 240;
export const REFERENCE_CAR_CIRCUITY = 1.3;
/** `model.json` → `mobility`. */
export const REFERENCE_NO_CAR_SHARE = 0.35;
export const REFERENCE_TWO_WHEEL_SHARE = 0.3;
export const REFERENCE_TWO_WHEEL_SPEED_KPH = 15.12;
export const REFERENCE_TWO_WHEEL_REACH_M = 7000;
export const REFERENCE_TWO_WHEEL_PER_KM_EUR = 0.03;

export interface ChoiceConfig {
  valueOfTimeSPerEur: number;
  transitConstant: number;
  carConstant: number;
  walkConstant: number;
  bikeConstant: number;
  transitFareWeight: number;
  transitWaitWeight: number;
  transitBiasMinutes: number;
  transitStageAccessWeight: number;
  transitStageEgressWeight: number;
  transitStageTransferWalkWeight: number;
  transitBurdenFirstMin: number;
  transitBurdenMultipleMin: number;
  carCostPerKmEur: number;
  carParkingEur: number;
  carParkingMinutes: number;
  carCircuity: number;
  walkCircuity: number;
  bikeCircuity: number;
  bikeCostPerKmEur: number;
  bikeFixedMinutes: number;
  bikeTimeFactor: number;
  bikeReachM: number;
  twoWheelShare: number;
  walkSpeedKph: number;
  bikeSpeedKph: number;
  noCarShare: number;
  noCarEffectiveness: number;
  minSensibleDrivingM: number;
  arrivalGapMinutes: number;
}

export const DEFAULT_CHOICE_CONFIG: ChoiceConfig = {
  valueOfTimeSPerEur: REFERENCE_VOT_S_PER_EUR,
  transitConstant: 0,
  carConstant: 0,
  walkConstant: 0,
  bikeConstant: 0,
  transitFareWeight: 0,
  transitWaitWeight: REFERENCE_WAIT_WEIGHT,
  transitBiasMinutes: REFERENCE_TRANSFER_RIDER_BIAS_S / 60,
  transitStageAccessWeight: REFERENCE_TRANSIT_STAGE_ACCESS_WEIGHT,
  transitStageEgressWeight: REFERENCE_TRANSIT_STAGE_EGRESS_WEIGHT,
  transitStageTransferWalkWeight: REFERENCE_TRANSIT_STAGE_TRANSFER_WALK_WEIGHT,
  transitBurdenFirstMin: REFERENCE_TRANSIT_BURDEN_FIRST_MIN,
  transitBurdenMultipleMin: REFERENCE_TRANSIT_BURDEN_MULTIPLE_MIN,
  carCostPerKmEur: REFERENCE_CAR_COST_PER_KM_EUR,
  carParkingEur: REFERENCE_CAR_PARKING_EUR,
  carParkingMinutes: REFERENCE_CAR_PARKING_S / 60,
  carCircuity: REFERENCE_CAR_CIRCUITY,
  walkCircuity: 1.33 * REFERENCE_TRANSFER_WALK_MULTIPLIER,
  bikeCircuity: 1.25,
  bikeCostPerKmEur: REFERENCE_TWO_WHEEL_PER_KM_EUR,
  bikeFixedMinutes: REFERENCE_CAR_PARKING_S / 60,
  bikeTimeFactor: 1.5,
  bikeReachM: REFERENCE_TWO_WHEEL_REACH_M,
  twoWheelShare: REFERENCE_TWO_WHEEL_SHARE,
  walkSpeedKph: 5.0,
  bikeSpeedKph: REFERENCE_TWO_WHEEL_SPEED_KPH,
  noCarShare: REFERENCE_NO_CAR_SHARE,
  noCarEffectiveness: REFERENCE_NO_CAR_EFFECTIVENESS,
  minSensibleDrivingM: GAME_MIN_SENSIBLE_DRIVING_DISTANCE_M,
  arrivalGapMinutes: GAME_ARRIVAL_GAP_S / 60,
};

/** Mirrors `ChoiceConfig.__post_init__`: throws instead of clamping silently. */
export function validateChoiceConfig(config: ChoiceConfig): void {
  const positive = (name: string, value: number) => {
    if (!(value > 0)) throw new Error(`${name} must be positive`);
  };
  const nonNegative = (name: string, value: number) => {
    if (!(value >= 0)) throw new Error(`${name} must be non-negative`);
  };
  const unit = (name: string, value: number) => {
    if (!(value >= 0 && value <= 1)) throw new Error(`${name} must be in [0, 1]`);
  };
  positive("value_of_time_s_per_eur", config.valueOfTimeSPerEur);
  nonNegative("transit_fare_weight", config.transitFareWeight);
  nonNegative("transit_wait_weight", config.transitWaitWeight);
  nonNegative("transit_bias_minutes", config.transitBiasMinutes);
  positive("transit_stage_access_weight", config.transitStageAccessWeight);
  positive("transit_stage_egress_weight", config.transitStageEgressWeight);
  positive("transit_stage_transfer_walk_weight", config.transitStageTransferWalkWeight);
  if (
    config.transitBurdenFirstMin < 0 ||
    config.transitBurdenMultipleMin < config.transitBurdenFirstMin
  ) {
    throw new Error("Transit transfer burdens are invalid");
  }
  nonNegative("car_cost_per_km_eur", config.carCostPerKmEur);
  nonNegative("bike_cost_per_km_eur", config.bikeCostPerKmEur);
  nonNegative("car_parking_eur", config.carParkingEur);
  nonNegative("car_parking_minutes", config.carParkingMinutes);
  positive("car_circuity", config.carCircuity);
  positive("walk_circuity", config.walkCircuity);
  positive("bike_circuity", config.bikeCircuity);
  nonNegative("bike_fixed_minutes", config.bikeFixedMinutes);
  positive("bike_time_factor", config.bikeTimeFactor);
  nonNegative("bike_reach_m", config.bikeReachM);
  positive("walk_speed_kph", config.walkSpeedKph);
  positive("bike_speed_kph", config.bikeSpeedKph);
  unit("no_car_share", config.noCarShare);
  unit("no_car_effectiveness", config.noCarEffectiveness);
  unit("two_wheel_share", config.twoWheelShare);
  nonNegative("min_sensible_driving_m", config.minSensibleDrivingM);
  nonNegative("arrival_gap_minutes", config.arrivalGapMinutes);
}

function timeCoefficient(config: ChoiceConfig): number {
  return 60.0 / config.valueOfTimeSPerEur;
}

export function transferBurdenMinutes(config: ChoiceConfig, transfers: number): number {
  if (transfers <= 0) return 0;
  if (transfers === 1) return config.transitBurdenFirstMin;
  return config.transitBurdenMultipleMin;
}

export interface TransitGeneralizedInput {
  inVehicleMin: number;
  waitMin?: number;
  accessWalkMin?: number;
  egressWalkMin?: number;
  transferWalkMin?: number;
  transfers?: number;
}

/**
 * Generalized transit cost in in-vehicle-time minutes.
 *
 * Stage walking weights already include the Wardman walk multiplier; the
 * non-linear 1 vs 2+ transfer burden follows Ha et al. (2020).
 */
export function transitGeneralizedMinutes(
  config: ChoiceConfig,
  input: TransitGeneralizedInput,
): number {
  return (
    Math.max(0, input.inVehicleMin)
    + config.transitWaitWeight * Math.max(0, input.waitMin ?? 0)
    + config.transitStageAccessWeight * Math.max(0, input.accessWalkMin ?? 0)
    + config.transitStageEgressWeight * Math.max(0, input.egressWalkMin ?? 0)
    + config.transitStageTransferWalkWeight * Math.max(0, input.transferWalkMin ?? 0)
    + transferBurdenMinutes(config, input.transfers ?? 0)
    + config.arrivalGapMinutes * (input.transfers ?? 0)
    + config.transitBiasMinutes
  );
}

export interface ModeUtilities {
  walk: number;
  car: number;
  transit: number;
  bike: number;
  rest: number;
}

export interface UtilitiesInput {
  walkTimeMin: number;
  carTimeMin: number;
  /** `null` means "no transit connection at all" and yields -Infinity. */
  transitTimeMin: number | null;
  bikeTimeMin?: number | null;
  transitWaitMin?: number;
  transitFare?: number;
  carDistanceKm?: number;
  bikeDistanceKm?: number | null;
  baseTimeMin?: number | null;
  transitAccessWalkMin?: number;
  transitEgressWalkMin?: number;
  transitTransferWalkMin?: number;
  transitTransfers?: number;
}

export function utilities(input: UtilitiesInput, config: ChoiceConfig = DEFAULT_CHOICE_CONFIG): ModeUtilities {
  const coefficient = timeCoefficient(config);
  const transit = input.transitTimeMin === null
    ? Number.NEGATIVE_INFINITY
    : config.transitConstant
      - coefficient * transitGeneralizedMinutes(config, {
        inVehicleMin: input.transitTimeMin,
        waitMin: input.transitWaitMin,
        accessWalkMin: input.transitAccessWalkMin,
        egressWalkMin: input.transitEgressWalkMin,
        transferWalkMin: input.transitTransferWalkMin,
        transfers: input.transitTransfers,
      })
      - config.transitFareWeight * (input.transitFare ?? 0);

  const hasBikeDistance = input.bikeDistanceKm !== undefined && input.bikeDistanceKm !== null;
  const bikeDistanceKm = !hasBikeDistance ? 0 : Math.max(0, input.bikeDistanceKm!);
  const bikeDistanceM = bikeDistanceKm * 1000 * config.bikeCircuity;
  const excessDistanceM = Math.max(0, bikeDistanceM - config.bikeReachM);
  // Without a distance the caller supplies the time directly; with one the
  // distance drives the time, matching choice.py exactly.
  const bikeTravelMinutes = !hasBikeDistance && input.bikeTimeMin !== undefined && input.bikeTimeMin !== null
    ? Math.max(0, input.bikeTimeMin)
    : (bikeDistanceM / config.bikeSpeedKph) * 60 / 1000;
  const excessTravelMinutes = (excessDistanceM / config.bikeSpeedKph) * 60 / 1000;
  const bikeGeneralizedMinutes =
    config.bikeFixedMinutes
    + config.bikeTimeFactor * (bikeTravelMinutes + excessTravelMinutes)
    + (bikeDistanceM / 1000) * config.bikeCostPerKmEur * config.valueOfTimeSPerEur / 60;
  const bike = config.bikeConstant - coefficient * bikeGeneralizedMinutes;

  const walkGeneralizedMinutes = config.walkCircuity * input.walkTimeMin;
  const carDistanceKm = input.carDistanceKm ?? 0;
  // Короткая поездка неудобна: заводить, разгоняться, искать парковку, -
  // поэтому время вождения умножается на коэффициент из игры. При
  // неизвестном расстоянии коэффициент не применяется: ноль километров
  // означает «нет данных», а не «поездка нулевой длины».
  const carDistanceM = carDistanceKm * 1000;
  const shortDriveMultiplier = config.minSensibleDrivingM > 0 && carDistanceM > 0
    ? shortDrivePenalty(carDistanceM)
    : 1;
  const carGeneralizedMinutes =
    config.carCircuity * input.carTimeMin * shortDriveMultiplier
    + config.carParkingMinutes
    + (carDistanceKm * config.carCostPerKmEur + config.carParkingEur)
    * config.valueOfTimeSPerEur
    / 60;
  const rest = input.baseTimeMin === undefined || input.baseTimeMin === null
    ? Number.NEGATIVE_INFINITY
    : config.transitConstant - coefficient * Math.max(0, input.baseTimeMin);

  return {
    walk: config.walkConstant - coefficient * walkGeneralizedMinutes,
    car: config.carConstant - coefficient * carGeneralizedMinutes,
    transit,
    bike,
    rest,
  };
}

export type ModeShare = "transit" | "car" | "walk" | "bike" | "rest";
export type ModeProbabilities = Record<ModeShare, number>;

const EMPTY_PROBABILITIES: ModeProbabilities = {
  transit: 0, car: 0, walk: 0, bike: 0, rest: 0,
};

export interface GameProbabilitiesInput {
  walkTimeMin: number;
  carTimeMin: number;
  transitInVehicleMin: number | null;
  transitWaitMin?: number;
  transitFare?: number;
  carDistanceKm?: number;
  transitAccessWalkMin?: number;
  transitEgressWalkMin?: number;
  transitTransferWalkMin?: number;
  transitTransfers?: number;
  bikeTimeMin?: number;
  bikeDistanceKm?: number;
  carAvailability?: number;
  restTimeMin?: number | null;
  transitAvailable?: boolean;
  population?: number;
  applyMinTransitChoice?: boolean;
}

/**
 * Модальный сплит по правилу игры: минимум обобщённой стоимости.
 *
 * Port of `game_probabilities` в `src/transit_planner/choice.py`. Случайной
 * полезности нет: доля режима получается интегрированием по детерминированной
 * лестнице дохода, поэтому у разных ступеней дохода разные победители.
 *
 * Велосипед - добавка планировщика, в игре его нет: он отбирается из доли
 * ходьбы и потому не увеличивает активный спрос.
 */
export function gameProbabilities(
  input: GameProbabilitiesInput,
  _config: ChoiceConfig = DEFAULT_CHOICE_CONFIG,
): ModeProbabilities {
  const carDistanceKm = input.carDistanceKm ?? 0;
  const carDistanceM = carDistanceKm * 1000;
  const transitPerceivedSec = input.transitAvailable === false ? 0 : (
    (input.transitInVehicleMin ?? 0) * 60
    + (input.transitWaitMin ?? 0) * 60 * GAME_WAIT_MIN
    + (input.transitAccessWalkMin ?? 0) * 60 * GAME_WALK_MIN
    + (input.transitEgressWalkMin ?? 0) * 60 * GAME_WALK_MIN
    + (input.transitTransferWalkMin ?? 0) * 60 * GAME_WALK_MIN
    + GAME_ARRIVAL_GAP_S * GAME_WAIT_MIN * (1 + Math.max(0, (input.transitTransfers ?? 0) - 1))
  );
  const carPerceivedSec = perceivedDrivingSeconds(input.carTimeMin * 60);
  const walkPerceivedSec = perceivedWalkOnlySeconds(input.walkTimeMin * 60);

  const split = gameModeSplit({
    transitPerceivedSec,
    carPerceivedSec,
    walkPerceivedSec,
    carDistanceM,
    transitFare: input.transitFare ?? 0,
    transitAvailable: input.transitAvailable !== false,
    carAvailability: input.carAvailability ?? 1,
    restTimeMin: input.restTimeMin ?? null,
    population: input.population ?? GAME_INCOME_LADDER_SIZE,
    applyMinTransitChoice: input.applyMinTransitChoice !== false,
  });

  const result: ModeProbabilities = {
    transit: split.transit,
    car: split.car,
    walk: split.walk,
    bike: 0,
    rest: split.rest,
  };
  if (input.bikeTimeMin !== undefined || input.bikeDistanceKm !== undefined) {
    const bikeUtility = input.bikeDistanceKm !== undefined
      ? Math.max(0, input.bikeDistanceKm) * 1.25 / 15.12 * 60
      : Math.max(0, input.bikeTimeMin ?? 0);
    const active = walkPerceivedSec + bikeUtility;
    if (active > 0) {
      const bikeShare = split.walk * bikeUtility / active;
      result.bike = bikeShare;
      result.walk = split.walk - bikeShare;
    }
  }
  return result;
}

export interface ProbabilitiesInput {
  carAvailability?: number;
  bikeAvailability?: number;
  noCarShare?: number;
}

export function probabilities(
  values: ModeUtilities,
  input: ProbabilitiesInput = {},
): ModeProbabilities {
  const carAvailability = input.carAvailability ?? 1;
  const bikeAvailability = input.bikeAvailability ?? 1;
  const noCarShare = input.noCarShare ?? REFERENCE_NO_CAR_SHARE;
  const unit = (name: string, value: number) => {
    if (!(value >= 0 && value <= 1)) throw new Error(`${name} must be in [0, 1]`);
  };
  unit("car_availability", carAvailability);
  unit("bike_availability", bikeAvailability);
  unit("no_car_share", noCarShare);

  const entries: Array<[ModeShare, number, number]> = [
    ["transit", values.transit, 1],
    ["car", values.car, carAvailability],
    ["walk", values.walk, 1],
    ["bike", values.bike, bikeAvailability],
    ["rest", values.rest, 1],
  ];
  const finite = entries.filter(([, value]) => value !== Number.NEGATIVE_INFINITY);
  if (finite.length === 0) return { ...EMPTY_PROBABILITIES };
  const maximum = Math.max(...finite.map(([, value]) => value));

  let transit = 0;
  let car = 0;
  let walk = 0;
  let bike = 0;
  let rest = 0;
  for (const [key, value, availability] of entries) {
    const weight = value === Number.NEGATIVE_INFINITY ? 0 : Math.exp(value - maximum) * availability;
    if (key === "transit") transit = weight;
    else if (key === "car") car = weight;
    else if (key === "walk") walk = weight;
    else if (key === "bike") bike = weight;
    else rest = weight;
  }

  const active = walk + bike;
  const withoutCar = transit + active + rest;
  const withCar = withoutCar + car;
  if (withCar <= 0) return { ...EMPTY_PROBABILITIES };

  // Группировка множителей повторяет choice.py: сначала умножение, потом
  // деление. Иначе меняется порядок округления float.
  const denominatorWithoutCar = Math.max(withoutCar, 1e-300);
  const activeShare =
    (1 - noCarShare) * active / withCar + noCarShare * active / denominatorWithoutCar;
  const bikeRatio = active > 0 ? bike / active : 0;

  return {
    transit: (1 - noCarShare) * transit / withCar + noCarShare * transit / denominatorWithoutCar,
    car: (1 - noCarShare) * car / withCar,
    walk: activeShare * (1 - bikeRatio),
    bike: activeShare * bikeRatio,
    rest: (1 - noCarShare) * rest / withCar + noCarShare * rest / denominatorWithoutCar,
  };
}

/**
 * Split transit demand by inverse generalized travel cost.
 *
 * Costs must already include stage-weighted walking, wait and transfer burden
 * (see {@link transitGeneralizedMinutes}).
 */
export function alternativeProbabilities(generalizedCosts: readonly number[]): number[] {
  if (generalizedCosts.length === 0) return [];
  const weights = generalizedCosts.map((value) => 1 / Math.max(1, value));
  const total = weights.reduce((sum, value) => sum + value, 0);
  if (!(total > 0)) return weights.map(() => 0);
  return weights.map((weight) => weight / total);
}
