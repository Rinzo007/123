/**
 * Assignment core: one demand-to-flow pass with strict-fit boarding.
 *
 * Port of `assignment.py::_assign_once` and its network-derived helpers.
 * Journeys are injected per OD pair instead of being routed here, so the
 * algorithm is a pure function of (demand, journeys, capacity, config) and can
 * be compared against the Python reference journey-for-journey — the routers
 * differ by design, the accounting must not.
 *
 * `tests/test_assignment_core_parity.py` feeds both sides identical journeys.
 */

/** `model.json` → `modes`; the fields assignment needs. */
export interface ModeProfile {
  capacity: number;
  dwellS: number;
  dwellPerPassengerS: number;
  jitterS: number;
  trackCapacityPerHour: number;
  accessM: number;
  platformM: number;
}

export const MODE_PROFILES: Record<string, ModeProfile> = {
  bus: {
    capacity: 90, dwellS: 20, dwellPerPassengerS: 2, jitterS: 90,
    trackCapacityPerHour: 90, accessM: 500, platformM: 0,
  },
  tram: {
    capacity: 250, dwellS: 25, dwellPerPassengerS: 0.6, jitterS: 60,
    trackCapacityPerHour: 40, accessM: 600, platformM: 40,
  },
  metro: {
    capacity: 750, dwellS: 30, dwellPerPassengerS: 0.15, jitterS: 15,
    trackCapacityPerHour: 30, accessM: 800, platformM: 100,
  },
  rail: {
    capacity: 1000, dwellS: 45, dwellPerPassengerS: 0.3, jitterS: 25,
    trackCapacityPerHour: 20, accessM: 1500, platformM: 140,
  },
};

export const CROWDED_LOAD_RATIO = 1.0;
export const SEVERE_LOAD_RATIO = 2.0;
export const EXTREME_LOAD_RATIO = 4.0;

export function loadRatio(passengers: number, capacity: number): number {
  return capacity <= 0 ? 0 : passengers / capacity;
}

export function crowdingLevel(ratio: number): "normal" | "crowded" | "severe" | "extreme" {
  if (ratio >= EXTREME_LOAD_RATIO) return "extreme";
  if (ratio >= SEVERE_LOAD_RATIO) return "severe";
  if (ratio >= CROWDED_LOAD_RATIO) return "crowded";
  return "normal";
}

/** Active reference crowding multiplier for in-vehicle time. */
export function crowdingTimeMultiplier(ratio: number): number {
  if (ratio < 0) throw new Error("load_ratio cannot be negative");
  return 1 + Math.max(0, Math.min(ratio, 1.5) - 0.85) * 2.2;
}

export interface HeadwayUnevennessInput {
  mode: string;
  headwayMin: number;
  periodHours: number;
  stopBoardings: readonly number[];
  routeClosed?: boolean;
  bothWays?: boolean;
}

/** Reference demand-feedback multiplier for headway. */
export function headwayUnevennessFactor(input: HeadwayUnevennessInput): number {
  if (!(input.headwayMin > 0) || !(input.periodHours > 0)) {
    throw new Error("headway_min and period_hours must be positive");
  }
  if (input.stopBoardings.some((value) => value < 0)) {
    throw new Error("stop_boardings cannot be negative");
  }
  const profile = MODE_PROFILES[input.mode];
  if (!profile) throw new Error(`Unknown mode: ${input.mode}`);
  const directionFactor = !input.routeClosed || input.bothWays ? 2 : 1;
  const f = input.stopBoardings.reduce(
    (sum, boardings) =>
      sum + (profile.dwellPerPassengerS * boardings)
      / (2 * directionFactor * input.periodHours * 3600),
    0,
  );
  const m = Math.min(1, (profile.jitterS * Math.exp(f)) / (input.headwayMin * 60));
  return 1 + m * m;
}

export interface SectionCapacity {
  routeId: string;
  fromStopId: string;
  toStopId: string;
  capacity: number;
}

export interface JourneyLeg {
  kind: "transit" | "walk";
  routeId?: string | null;
  serviceId?: string | null;
  fromId: string;
  toId: string;
  durationMin: number;
  waitMin: number;
}

export interface AssignmentJourney {
  legs: JourneyLeg[];
  transfers: number;
  durationMin: number;
}

export interface AssignmentZone {
  id: string;
  centroidX: number;
  centroidY: number;
  population: number;
  jobs: number;
}

export interface AssignmentPair {
  originZoneId: string;
  destinationZoneId: string;
  tripsPerDay: number;
  baseTimeMin: number | null;
}

export interface AssignmentConfig {
  carSpeedKph: number;
  walkingSpeedKph: number;
  crowdingStartRatio: number;
  iterations: number;
  damping: number;
  transitFare: number;
  convergenceTolerance: number;
  maxAccessDistanceM: number;
  maxTransitAlternatives: number;
  alternativeDiversityPenaltyMin: number;
}

export const DEFAULT_ASSIGNMENT_CONFIG: AssignmentConfig = {
  carSpeedKph: 30.0,
  walkingSpeedKph: 5.4,
  crowdingStartRatio: 0.85,
  iterations: 6,
  damping: 0.5,
  transitFare: 0.0,
  convergenceTolerance: 1e-4,
  maxAccessDistanceM: 1500.0,
  maxTransitAlternatives: 3,
  alternativeDiversityPenaltyMin: 15.0,
};

export interface SectionLoad {
  routeId: string;
  fromStopId: string;
  toStopId: string;
  passengers: number;
  capacity: number;
  deniedBoardings: number;
  loadRatio: number;
  crowdingLevel: string;
}

export interface RouteFlow {
  routeId: string;
  boardings: number;
  passengerSectionTraversals: number;
}

export interface StopFlow {
  stopId: string;
  boardings: number;
  alightings: number;
  transfers: number;
  dwellSeconds: number;
  platformM: number;
}

export interface AssignmentMetrics {
  totalTrips: number;
  transitTrips: number;
  carTrips: number;
  walkTrips: number;
  transitShare: number;
  averageTransitTimeMin: number;
  averageTransfers: number;
  averageWaitTimeMin: number;
  deniedBoardings: number;
}

export interface AssignmentSnapshot {
  metrics: AssignmentMetrics;
  routeFlows: RouteFlow[];
  sectionLoads: SectionLoad[];
  stopFlows: StopFlow[];
  unserved: number;
  lossReasons: Array<{ reason: string; trips: number }>;
  serviceStopBoardings: Array<[string, string, number]>;
}

export function validateAssignmentConfig(config: AssignmentConfig): void {
  const positive = (name: string, value: number) => {
    if (!(value > 0)) throw new Error(`${name} must be positive`);
  };
  const nonNegative = (name: string, value: number) => {
    if (!(value >= 0)) throw new Error(`${name} must be non-negative`);
  };
  positive("car_speed_kph", config.carSpeedKph);
  positive("walking_speed_kph", config.walkingSpeedKph);
  nonNegative("crowding_start_ratio", config.crowdingStartRatio);
  positive("iterations", config.iterations);
  if (!(config.damping > 0 && config.damping <= 1)) {
    throw new Error("damping must be in (0, 1]");
  }
  nonNegative("transit_fare", config.transitFare);
  positive("convergence_tolerance", config.convergenceTolerance);
  nonNegative("max_access_distance_m", config.maxAccessDistanceM);
  positive("max_transit_alternatives", config.maxTransitAlternatives);
  nonNegative("alternative_diversity_penalty_min", config.alternativeDiversityPenaltyMin);
}

/** Zone-centroid to stop walking time, zero when either side is absent. */
export function walkMinutes(
  zone: AssignmentZone | undefined,
  stop: { x: number; y: number } | undefined,
  walkingSpeedKph: number,
): number {
  if (!zone || !stop || !(walkingSpeedKph > 0)) return 0;
  const distanceM = Math.hypot(zone.centroidX - stop.x, zone.centroidY - stop.y);
  return (distanceM / 1000 / walkingSpeedKph) * 60;
}

export function distanceBetweenZones(
  pair: AssignmentPair,
  zones: Map<string, AssignmentZone>,
): number {
  const origin = zones.get(pair.originZoneId);
  const destination = zones.get(pair.destinationZoneId);
  if (!origin || !destination) {
    return pair.originZoneId === pair.destinationZoneId ? 0 : 1000;
  }
  return Math.hypot(origin.centroidX - destination.centroidX, origin.centroidY - destination.centroidY);
}

export interface DemandLossInput {
  transitTime: number;
  walkTime: number;
  carTime: number;
  transfers: number;
  waitMin: number;
  transitFare: number;
  fareWeight: number;
  routePenalized: boolean;
}

export function classifyDemandLoss(input: DemandLossInput): string {
  if (input.routePenalized) return "crowd";
  if (input.waitMin > 0.5 * input.transitTime) return "wait";
  const bestAlternative = Math.min(input.walkTime, input.carTime);
  if (input.transitFare > 0 && input.fareWeight * input.transitFare >= 0.5 * input.transitTime) {
    return "price";
  }
  if (input.transfers > 0 && input.transitTime > bestAlternative) return "transfer";
  return "ride";
}

export interface AssignOnceInput {
  pairs: readonly AssignmentPair[];
  /** Alternatives per pair, already filtered to journeys with a transit leg. */
  journeysPerPair: ReadonlyArray<readonly AssignmentJourney[]>;
  /** Door-to-door access walk per pair, parallel to `pairs` (router output). */
  accessWalkMin: readonly number[];
  /** Door-to-door egress walk per pair, parallel to `pairs` (router output). */
  egressWalkMin: readonly number[];
  zones: Map<string, AssignmentZone>;
  /** Every route in the network: `route_flows` must report unused ones too. */
  routeIds: readonly string[];
  /** Every stop in the network: `stop_flows` must report unused ones too. */
  stopIds: readonly string[];
  sectionCapacity: readonly SectionCapacity[];
  stopPlatformM: ReadonlyMap<string, number>;
  config: AssignmentConfig;
  choice: import("./choice").ChoiceConfig;
  segmentCrowdingPenalties: ReadonlyMap<string, number>;
  stopDwell: (stopId: string, boardings: number) => number;
  totalTripsPerDay: number;
}

const SEGMENT_KEY = (routeId: string, fromId: string, toId: string) => `${routeId}|${fromId}|${toId}`;

export function assignOnce(input: AssignOnceInput): AssignmentSnapshot {
  const { config, choice, pairs, zones } = input;
  const sectionFlow = new Map<string, number>();
  const sectionDenied = new Map<string, number>();
  const serviceStopBoardings = new Map<string, number>();
  const routeBoardings = new Map<string, number>();
  const routeTraversals = new Map<string, number>();
  const stopBoardings = new Map<string, number>();
  const stopAlightings = new Map<string, number>();
  const stopTransfers = new Map<string, number>();
  const capacityByKey = new Map<string, number>();
  for (const section of input.sectionCapacity) {
    capacityByKey.set(SEGMENT_KEY(section.routeId, section.fromStopId, section.toStopId), section.capacity);
  }

  let totalTransit = 0;
  let totalCar = 0;
  let totalWalk = 0;
  let deniedBoardings = 0;
  let weightedTransitTime = 0;
  let weightedTransfers = 0;
  let weightedWait = 0;
  let unserved = 0;
  const lossReasons = new Map<string, number>();

  const add = (map: Map<string, number>, key: string, value: number) => {
    map.set(key, (map.get(key) ?? 0) + value);
  };

  pairs.forEach((pair, pairIndex) => {
    const trips = pair.tripsPerDay;
    if (trips <= 0) return;

    const distanceM = distanceBetweenZones(pair, zones);
    const walkTime = (distanceM / 1000 / config.walkingSpeedKph) * 60;
    const carTime = (distanceM / 1000 / config.carSpeedKph) * 60;

    const accessWalkMin = input.accessWalkMin[pairIndex] ?? 0;
    const egressWalkMin = input.egressWalkMin[pairIndex] ?? 0;

    const journeys = input.journeysPerPair[pairIndex] ?? [];
    const generalized: number[] = [];
    const waits: number[] = [];
    const inVehicles: number[] = [];
    const transferWalks: number[] = [];
    for (const journey of journeys) {
      const inVehicle = journey.legs
        .filter((leg) => leg.kind === "transit")
        .reduce((sum, leg) => sum + leg.durationMin, 0);
      const wait = journey.legs
        .filter((leg) => leg.kind === "transit")
        .reduce((sum, leg) => sum + leg.waitMin, 0);
      const transferWalk = journey.legs
        .filter((leg) => leg.kind === "walk")
        .reduce((sum, leg) => sum + leg.durationMin, 0);
      generalized.push(
        transitGeneralized(choice, {
          inVehicleMin: inVehicle,
          waitMin: wait,
          accessWalkMin,
          egressWalkMin,
          transferWalkMin: transferWalk,
          transfers: journey.transfers,
        }),
      );
      waits.push(wait);
      inVehicles.push(inVehicle);
      transferWalks.push(transferWalk);
    }

    const best = journeys[0];
    const bestInVehicle = inVehicles[0] ?? 0;
    const bestWait = waits[0] ?? 0;
    const bestTransfers = best?.transfers ?? 0;
    const bestTransferWalk = transferWalks[0] ?? 0;
    const transitTime = generalized.length > 0 ? generalized[0] : null;

    const probs = gameProbabilities({
      walkTimeMin: walkTime,
      carTimeMin: carTime,
      transitInVehicleMin: best ? bestInVehicle : null,
      transitWaitMin: bestWait,
      transitFare: config.transitFare,
      carDistanceKm: distanceM / 1000,
      transitAccessWalkMin: accessWalkMin,
      transitEgressWalkMin: egressWalkMin,
      transitTransferWalkMin: bestTransferWalk,
      transitTransfers: bestTransfers,
      // `best` - это undefined при пустом journeys, а не null, поэтому
      // строгое сравнение с null считало бы транзит доступным.
      transitAvailable: best != null,
    }, choice);

    const transitTrips = trips * probs.transit;
    const alternativeShares = journeys.length > 0 ? alternativeProbabilities(generalized) : [];
    const carTrips = trips * probs.car;
    const walkTrips = trips * probs.walk;
    totalTransit += transitTrips;
    totalCar += carTrips;
    totalWalk += walkTrips;

    if (!best) {
      unserved += transitTrips;
      if (transitTrips > 0) add(lossReasons, "noroute", transitTrips);
      return;
    }

    const lostTrips = Math.max(0, trips - transitTrips);
    if (lostTrips > 0) {
      const routePenalized = best.legs.some(
        (leg) => leg.kind === "transit" && (input.segmentCrowdingPenalties.get(
          SEGMENT_KEY(leg.routeId ?? "", leg.fromId, leg.toId),
        ) ?? 0) > 0,
      );
      const reason = classifyDemandLoss({
        transitTime: generalized[0] ?? 0,
        walkTime,
        carTime,
        transfers: bestTransfers,
        waitMin: bestWait,
        transitFare: config.transitFare,
        fareWeight: choice.transitFareWeight,
        routePenalized,
      });
      add(lossReasons, reason, lostTrips);
    }

    journeys.forEach((journey, alternativeIndex) => {
      const candidateTrips = transitTrips * (alternativeShares[alternativeIndex] ?? 0);
      weightedTransitTime += candidateTrips * generalized[alternativeIndex];
      weightedTransfers += candidateTrips * journey.transfers;
      weightedWait += candidateTrips * waits[alternativeIndex];

      journey.legs.forEach((leg, index) => {
        if (leg.kind !== "transit" || !leg.routeId) return;
        const key = SEGMENT_KEY(leg.routeId, leg.fromId, leg.toId);
        const segmentCapacity = capacityByKey.get(key) ?? 0;
        add(sectionFlow, key, candidateTrips);
        if (leg.serviceId) {
          add(serviceStopBoardings, `${leg.serviceId}|${leg.fromId}`, candidateTrips);
        }
        add(routeTraversals, leg.routeId, candidateTrips);

        const previousLeg = index > 0 ? journey.legs[index - 1] : null;
        const nextLeg = index + 1 < journey.legs.length ? journey.legs[index + 1] : null;
        const previousSameRoute = previousLeg !== null
          && previousLeg.kind === "transit"
          && previousLeg.routeId === leg.routeId;
        const nextSameRoute = nextLeg !== null
          && nextLeg.kind === "transit"
          && nextLeg.routeId === leg.routeId;

        if (!previousSameRoute) {
          add(routeBoardings, leg.routeId, candidateTrips);
          add(stopBoardings, leg.fromId, candidateTrips);
          if (previousLeg !== null && previousLeg.kind === "walk") {
            add(stopTransfers, leg.fromId, candidateTrips);
          }
        }
        if (!nextSameRoute) {
          add(stopAlightings, leg.toId, candidateTrips);
          if (nextLeg !== null && nextLeg.kind === "walk") {
            add(stopTransfers, leg.toId, candidateTrips);
          }
        }

        // Strict-fit boarding: a vehicle cannot take more than its free
        // capacity after alighting. Excess demand is denied and reported, not
        // silently dropped from the totals.
        if (!previousSameRoute) {
          const alightingHere = stopAlightings.get(leg.fromId) ?? 0;
          const freeCapacity = Math.max(0, segmentCapacity - alightingHere);
          if (candidateTrips > freeCapacity) {
            const denied = candidateTrips - freeCapacity;
            deniedBoardings += denied;
            add(sectionDenied, key, denied);
          }
        }
      });
    });
  });

  const sectionLoads: SectionLoad[] = input.sectionCapacity.map((section) => {
    const key = SEGMENT_KEY(section.routeId, section.fromStopId, section.toStopId);
    const passengers = sectionFlow.get(key) ?? 0;
    const ratio = loadRatio(passengers, section.capacity);
    return {
      routeId: section.routeId,
      fromStopId: section.fromStopId,
      toStopId: section.toStopId,
      passengers,
      capacity: section.capacity,
      deniedBoardings: sectionDenied.get(key) ?? 0,
      loadRatio: ratio,
      crowdingLevel: crowdingLevel(ratio),
    };
  });

  const routeFlows: RouteFlow[] = input.routeIds.map((routeId) => ({
    routeId,
    boardings: routeBoardings.get(routeId) ?? 0,
    passengerSectionTraversals: routeTraversals.get(routeId) ?? 0,
  }));

  const stopFlows: StopFlow[] = input.stopIds.map((stopId) => {
    const boardings = stopBoardings.get(stopId) ?? 0;
    return {
      stopId,
      boardings,
      alightings: stopAlightings.get(stopId) ?? 0,
      transfers: stopTransfers.get(stopId) ?? 0,
      dwellSeconds: input.stopDwell(stopId, boardings),
      platformM: input.stopPlatformM.get(stopId) ?? 0,
    };
  });

  const total = input.totalTripsPerDay;
  return {
    metrics: {
      totalTrips: total,
      transitTrips: totalTransit,
      carTrips: totalCar,
      walkTrips: totalWalk,
      transitShare: total <= 0 ? 0 : totalTransit / total,
      averageTransitTimeMin: totalTransit <= 0 ? 0 : weightedTransitTime / totalTransit,
      averageTransfers: totalTransit <= 0 ? 0 : weightedTransfers / totalTransit,
      averageWaitTimeMin: totalTransit === 0 ? 0 : weightedWait / totalTransit,
      deniedBoardings,
    },
    routeFlows,
    sectionLoads,
    stopFlows,
    unserved,
    lossReasons: [...lossReasons.entries()]
      .map(([reason, trips]) => ({ reason, trips }))
      .sort((a, b) => (a.reason < b.reason ? -1 : a.reason > b.reason ? 1 : 0)),
    serviceStopBoardings: [...serviceStopBoardings.entries()]
      .map(([key, value]) => {
        const separator = key.indexOf("|");
        return [key.slice(0, separator), key.slice(separator + 1), value] as [string, string, number];
      })
      .sort((a, b) => (a[0] === b[0] ? (a[1] < b[1] ? -1 : a[1] > b[1] ? 1 : 0) : a[0] < b[0] ? -1 : 1)),
  };
}

import {
  alternativeProbabilities,
  gameProbabilities,
  probabilities,
  transitGeneralizedMinutes as transitGeneralized,
  utilities,
} from "./choice";

/**
 * Target crowding penalties from the observed section loads.
 *
 * Only sections above `crowdingStartRatio` are penalized, and the penalty is
 * proportional to the section's own run time so long and short sections carry
 * a proportionate in-vehicle-time penalty.
 */
export function segmentCrowdingPenalties(
  sectionLoads: readonly SectionLoad[],
  config: AssignmentConfig,
  runTimeMinByKey: ReadonlyMap<string, number>,
): Map<string, number> {
  const penalties = new Map<string, number>();
  for (const section of sectionLoads) {
    if (section.loadRatio <= config.crowdingStartRatio) continue;
    const key = SEGMENT_KEY(section.routeId, section.fromStopId, section.toStopId);
    const baseRuntime = runTimeMinByKey.get(key);
    if (baseRuntime === undefined) continue;
    penalties.set(
      key,
      (penalties.get(key) ?? 0)
      + baseRuntime * (crowdingTimeMultiplier(section.loadRatio) - 1),
    );
  }
  return penalties;
}

/** Blended update of the penalty/factor maps, mirroring the Python damping. */
export function blendFeedback(
  current: ReadonlyMap<string, number>,
  target: ReadonlyMap<string, number>,
  damping: number,
  fallback: number,
): Map<string, number> {
  const blended = new Map<string, number>();
  for (const [key, value] of target) {
    blended.set(key, (current.get(key) ?? fallback) * damping + value * (1 - damping));
  }
  return blended;
}

export function maxFeedbackDelta(
  left: ReadonlyMap<string, number>,
  right: ReadonlyMap<string, number>,
): number {
  let worst = 0;
  for (const key of new Set([...left.keys(), ...right.keys()])) {
    worst = Math.max(worst, Math.abs((left.get(key) ?? 0) - (right.get(key) ?? 0)));
  }
  return worst;
}
