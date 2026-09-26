export const REFERENCE = {
  votSecondsPerEuro: 360,
  noCarShare: 0.35,
  noCarEffectiveness: 0.78,
  twoWheelShare: 0.30,
  twoWheelSpeedKph: 15.12,
  twoWheelReachM: 7000,
  twoWheelCostPerKmEuro: 0.03,
  carCostPerKmEuro: 0.25,
  carParkingEuro: 1.5,
  carParkingMinutes: 4,
  carCircuity: 1.3,
  walkCircuity: 1.33,
  walkSpeedKph: 5,
  transitWaitMultiplier: 1,
  transitBiasMinutes: 0,
  referenceSpeedKph: 30,
} as const;

export const MODE_PROFILES = {
  bus:  { capacity: 90,  speedKph: 18, dwellS: 20, dwellPerPassengerS: 2, turnbackS: 60, trackCapacityTph: 90, accessM: 500 },
  tram: { capacity: 250, speedKph: 19, dwellS: 25, dwellPerPassengerS: 0.6, turnbackS: 90, trackCapacityTph: 40, accessM: 600 },
  metro:{ capacity: 750, speedKph: 70, dwellS: 30, dwellPerPassengerS: 0.15, turnbackS: 150, trackCapacityTph: 30, accessM: 800 },
  rail: { capacity: 1000,speedKph: 58, dwellS: 45, dwellPerPassengerS: 0.3, turnbackS: 300, trackCapacityTph: 20, accessM: 1500 },
} as const;

export type ChoiceUtilities = {
  transit: number;
  car: number;
  walk: number;
  bike: number;
  rest?: number;
};

export type ChoiceProbabilities = {
  transit: number;
  car: number;
  walk: number;
  bike: number;
  rest: number;
};

const finite = (value: number) => Number.isFinite(value) ? value : Number.NEGATIVE_INFINITY;

export function calculateUtilities(args: {
  walkTimeMin: number;
  carTimeMin: number;
  transitTimeMin: number | null;
  bikeTimeMin?: number | null;
  transitWaitMin?: number;
  transitFare?: number;
  carDistanceKm?: number;
  bikeDistanceKm?: number | null;
  baseTimeMin?: number | null;
  transitFareWeight?: number;
  transitWaitWeight?: number;
  transitBiasMinutes?: number;
  carCostPerKmEuro?: number;
  carParkingEuro?: number;
  carParkingMinutes?: number;
  carCircuity?: number;
  walkCircuity?: number;
  bikeCircuity?: number;
  bikeCostPerKmEuro?: number;
  bikeFixedMinutes?: number;
  bikeTimeFactor?: number;
  bikeReachM?: number;
  bikeSpeedKph?: number;
  votSecondsPerEuro?: number;
}): ChoiceUtilities {
  const c = {
    transitFareWeight: args.transitFareWeight ?? 0,
    transitWaitWeight: args.transitWaitWeight ?? REFERENCE.transitWaitMultiplier,
    transitBiasMinutes: args.transitBiasMinutes ?? REFERENCE.transitBiasMinutes,
    carCostPerKmEuro: args.carCostPerKmEuro ?? REFERENCE.carCostPerKmEuro,
    carParkingEuro: args.carParkingEuro ?? REFERENCE.carParkingEuro,
    carParkingMinutes: args.carParkingMinutes ?? REFERENCE.carParkingMinutes,
    carCircuity: args.carCircuity ?? REFERENCE.carCircuity,
    walkCircuity: args.walkCircuity ?? REFERENCE.walkCircuity,
    bikeCircuity: args.bikeCircuity ?? 1.25,
    bikeCostPerKmEuro: args.bikeCostPerKmEuro ?? REFERENCE.twoWheelCostPerKmEuro,
    bikeFixedMinutes: args.bikeFixedMinutes ?? REFERENCE.carParkingMinutes,
    bikeTimeFactor: args.bikeTimeFactor ?? 1.5,
    bikeReachM: args.bikeReachM ?? REFERENCE.twoWheelReachM,
    bikeSpeedKph: args.bikeSpeedKph ?? REFERENCE.twoWheelSpeedKph,
    vot: args.votSecondsPerEuro ?? REFERENCE.votSecondsPerEuro,
  };
  const coefficient = 60 / c.vot;
  const transitTime = args.transitTimeMin === null ? Number.NEGATIVE_INFINITY :
    -coefficient * (Math.max(0, args.transitTimeMin) + c.transitWaitWeight * Math.max(0, args.transitWaitMin ?? 0) + c.transitBiasMinutes) -
    c.transitFareWeight * Math.max(0, args.transitFare ?? 0);

  const hasBikeDistance = args.bikeDistanceKm != null;
  const bikeDistanceKm = Math.max(0, args.bikeDistanceKm ?? 0);
  const bikeDistanceM = bikeDistanceKm * 1000 * c.bikeCircuity;
  const excessDistanceM = Math.max(0, bikeDistanceM - c.bikeReachM);
  const bikeTravelMin = !hasBikeDistance && args.bikeTimeMin != null
    ? Math.max(0, args.bikeTimeMin)
    : bikeDistanceM / c.bikeSpeedKph * 60 / 1000;
  const excessTravelMin = excessDistanceM / c.bikeSpeedKph * 60 / 1000;
  const bikeGeneralizedMin =
    c.bikeFixedMinutes +
    c.bikeTimeFactor * (bikeTravelMin + excessTravelMin) +
    bikeDistanceM / 1000 * c.bikeCostPerKmEuro * c.vot / 60;

  return {
    transit: finite(transitTime),
    car: -coefficient * (
      c.carCircuity * Math.max(0, args.carTimeMin) +
      c.carParkingMinutes +
      (Math.max(0, args.carDistanceKm ?? 0) * c.carCostPerKmEuro + c.carParkingEuro) * c.vot / 60
    ),
    walk: -coefficient * c.walkCircuity * Math.max(0, args.walkTimeMin),
    bike: -coefficient * bikeGeneralizedMin,
    rest: args.baseTimeMin == null ? Number.NEGATIVE_INFINITY : -coefficient * Math.max(0, args.baseTimeMin),
  };
}

export function calculateProbabilities(
  values: ChoiceUtilities,
  options: { carAvailability?: number; bikeAvailability?: number; noCarShare?: number } = {},
): ChoiceProbabilities {
  const carAvailability = Math.min(1, Math.max(0, options.carAvailability ?? 1));
  const bikeAvailability = Math.min(1, Math.max(0, options.bikeAvailability ?? 1));
  const noCarShare = Math.min(1, Math.max(0, options.noCarShare ?? REFERENCE.noCarShare));
  const entries: Array<[keyof ChoiceProbabilities, number, number]> = [
    ["transit", finite(values.transit), 1],
    ["car", finite(values.car), carAvailability],
    ["walk", finite(values.walk), 1],
    ["bike", finite(values.bike), bikeAvailability],
    ["rest", finite(values.rest ?? Number.NEGATIVE_INFINITY), 1],
  ];
  const finiteValues = entries.map(([, value]) => value).filter(Number.isFinite);
  if (!finiteValues.length) return { transit: 0, car: 0, walk: 0, bike: 0, rest: 0 };
  const maximum = Math.max(...finiteValues);
  const weights: Record<string, number> = {};
  for (const [key, value, availability] of entries) weights[key] = Number.isFinite(value) ? Math.exp(Math.max(-50, Math.min(50, value - maximum))) * availability : 0;
  const transit = weights.transit ?? 0;
  const car = weights.car ?? 0;
  const active = (weights.walk ?? 0) + (weights.bike ?? 0);
  const rest = weights.rest ?? 0;
  const withCar = transit + car + active + rest;
  const withoutCar = transit + active + rest;
  if (withCar <= 0) return { transit: 0, car: 0, walk: 0, bike: 0, rest: 0 };
  const denominatorWithoutCar = Math.max(withoutCar, 1e-300);
  const activeShare = (1 - noCarShare) * active / withCar + noCarShare * active / denominatorWithoutCar;
  const bikeRatio = active > 0 ? (weights.bike ?? 0) / active : 0;
  return {
    transit: (1 - noCarShare) * transit / withCar + noCarShare * transit / denominatorWithoutCar,
    car: (1 - noCarShare) * car / withCar,
    walk: activeShare * (1 - bikeRatio),
    bike: activeShare * bikeRatio,
    rest: (1 - noCarShare) * rest / withCar + noCarShare * rest / denominatorWithoutCar,
  };
}

export function splitTransitAlternatives(
  alternatives: Array<{ timeMin: number; waitMin: number }>,
  transitWaitMultiplier = REFERENCE.transitWaitMultiplier,
  transitBiasMinutes = REFERENCE.transitBiasMinutes,
): number[] {
  if (!alternatives.length) return [];
  const weights = alternatives.map((alternative) => {
    const generalized = Math.max(
      1,
      Math.max(0, alternative.timeMin) +
        transitWaitMultiplier * Math.max(0, alternative.waitMin) +
        transitBiasMinutes,
    );
    return 1 / generalized;
  });
  const total = weights.reduce((sum, value) => sum + value, 0);
  return total > 0 ? weights.map((value) => value / total) : weights.map(() => 0);
}
