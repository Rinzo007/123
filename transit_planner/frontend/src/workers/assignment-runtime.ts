/**
 * Assignment iteration loop.
 *
 * Port of `assignment.py::assign_demand`: repeated `assignOnce` passes whose
 * crowding penalties and headway feedback are blended by `damping` until the
 * change falls under `convergenceTolerance`.
 *
 * Journeys come from the rRAPTOR kernel, which is imported directly instead of
 * through a nested worker: the loop has to re-route every OD pair on every
 * iteration, and one worker per pair per iteration would be untenable.
 *
 * Crowding steers the routing through `segmentPenalties`, and the resulting
 * loads feed the next iteration. Headway feedback is applied by re-packing the
 * network with the current factors, so the schedule itself moves.
 */
import {
  assignOnce,
  blendFeedback,
  DEFAULT_ASSIGNMENT_CONFIG,
  maxFeedbackDelta,
  segmentCrowdingPenalties,
  validateAssignmentConfig,
  walkMinutes,
  type AssignmentConfig,
  type AssignmentJourney,
  type AssignmentPair,
  type AssignmentSnapshot,
  type AssignmentZone,
  type SectionCapacity,
} from "../assignment-model";
import {
  nearestStopId,
  sectionCapacityAndPlatforms,
  segmentRunTimes,
  serviceHeadwayFactors,
  stopDwellCoefficients,
  stopDwellFromCoefficients,
} from "../assignment-network";
import { DEFAULT_CHOICE_CONFIG, type ChoiceConfig } from "../choice";
import { routeAlternatives, type RaptorRequest } from "./routing-kernel";
import { packRaptorInput, type RaptorRoutePattern } from "./routing";
import type { NetworkPayload } from "../types";

export interface AssignmentWorkerInput {
  job: number;
  network: NetworkPayload;
  periodId: string;
  pairs: AssignmentPair[];
  zones: AssignmentZone[];
  /** Departure minute of the first range departure. */
  departureMin: number;
  rangeWindowMin: number;
  maxTransfers: number;
  config?: Partial<AssignmentConfig>;
  choice?: Partial<ChoiceConfig>;
}

export interface AssignmentSectionRow {
  routeId: string;
  fromStopId: string;
  toStopId: string;
  passengers: number;
  capacity: number;
  loadRatio: number;
  crowdingLevel: string;
  deniedBoardings: number;
}

export interface AssignmentStopRow {
  stopId: string;
  boardings: number;
  alightings: number;
  transfers: number;
  dwellSeconds: number;
  platformM: number;
}

export interface AssignmentRouteRow {
  routeId: string;
  boardings: number;
  passengerSectionTraversals: number;
}

export interface AssignmentWorkerResult {
  type: "assigned";
  job: number;
  metrics: AssignmentSnapshot["metrics"];
  unserved: number;
  iterations: number;
  maxLoadRatio: number;
  lossReasons: Array<{ reason: string; trips: number }>;
  serviceHeadwayFactors: Array<[string, number]>;
  sectionLoads: AssignmentSectionRow[];
  stopFlows: AssignmentStopRow[];
  routeFlows: AssignmentRouteRow[];
  /** OD pairs that produced no transit journey at all. */
  unroutedPairs: number;
}

const SEGMENT_KEY = (routeId: string, fromId: string, toId: string) => `${routeId}|${fromId}|${toId}`;

/**
 * Expands a kernel ride into per-segment legs.
 *
 * The kernel reports a whole ride (board → alight), while assignment needs the
 * individual sections to attribute loads and capacity, exactly as the Python
 * reference emits one leg per segment.
 */
function rideToLegs(
  ride: { pattern: number; boardLocal: number; alightLocal: number; inVehicleMin: number; waitMin: number },
  pattern: RaptorRoutePattern,
  serviceId: string,
  segmentTimeByKey: ReadonlyMap<string, number>,
  stopIds: readonly string[],
): AssignmentJourney["legs"] {
  const legs: AssignmentJourney["legs"] = [];
  for (let local = ride.boardLocal; local < ride.alightLocal; local += 1) {
    const fromIndex = pattern.stops[local];
    const toIndex = pattern.stops[local + 1];
    if (fromIndex === undefined || toIndex === undefined) {
      throw new Error(`Pattern ${pattern.routeId} references a stop missing from the network`);
    }
    const fromId = stopIds[fromIndex];
    const toId = stopIds[toIndex];
    legs.push({
      kind: "transit",
      routeId: pattern.routeId,
      serviceId,
      fromId,
      toId,
      durationMin: segmentTimeByKey.get(SEGMENT_KEY(pattern.routeId, fromId, toId)) ?? 0,
      // Ожидание относится к посадке на весь рейс, поэтому оно приписывается
      // только первому сегменту: иначе оно умножалось бы на длину рейса.
      waitMin: local === ride.boardLocal ? ride.waitMin : 0,
    });
  }
  return legs;
}

export function assignDemand(input: AssignmentWorkerInput): AssignmentWorkerResult {
  const config = { ...DEFAULT_ASSIGNMENT_CONFIG, ...input.config };
  const choice = { ...DEFAULT_CHOICE_CONFIG, ...input.choice };
  // Проверка до любых вычислений: молчаливый неверный damping или ноль итераций
  // дали бы «нулевые» результаты вместо ошибки.
  validateAssignmentConfig(config);
  const period = input.network.periods.find((item) => item.id === input.periodId);
  if (!period) throw new Error(`Period not found in network: ${input.periodId}`);

  const stopIds = input.network.stops.map((stop) => stop.id);
  const stopIndex = new Map(stopIds.map((id, index) => [id, index]));
  const serviceByRoute = new Map<string, string[]>();
  for (const service of input.network.services) {
    if (service.headway_by_period[input.periodId] === undefined) continue;
    serviceByRoute.set(service.route_id, [...(serviceByRoute.get(service.route_id) ?? []), service.id]);
  }

  const { capacity, platformM } = sectionCapacityAndPlatforms(input.network, input.periodId);
  const sectionCapacity: SectionCapacity[] = capacity;
  const dwell = stopDwellCoefficients(input.network, input.periodId);
  // Времена сегментов нужны для развёртки рейса в ноги по секциям.
  const runTimes = segmentRunTimes(input.network);
  const zones = new Map(input.zones.map((zone) => [zone.id, zone]));
  const totalTrips = input.pairs.reduce((sum, pair) => sum + Math.max(0, pair.tripsPerDay), 0);

  // Ближайшая остановка зоны и ходьба к ней считаются один раз: сеть на итерации
  // не меняется, а пересчитывать их в каждом проходе бессмысленно.
  const zoneStops = new Map<string, number>();
  for (const zone of input.zones) {
    const stopId = nearestStopId(input.network, zone, config.maxAccessDistanceM, input.periodId);
    if (stopId === null) continue;
    const index = stopIndex.get(stopId);
    if (index === undefined) continue;
    zoneStops.set(zone.id, index);
  }

  // Итеративный цикл подмешивает в выбор штраф за тесноту секций и интервальные
  // множители. В игре (popCommuteWorker) обратной связи по загрузке нет:
  // getModeChoice берёт времена из расписания и времени суток. Цикл -
  // планировщиковая надстройка, а не зеркало игры.
  let segmentPenalties = new Map<string, number>();
  let headwayFactors = new Map<string, number>();
  let snapshot: AssignmentSnapshot | null = null;
  let convergedAfter = config.iterations;
  let unroutedPairs = 0;

  for (let iteration = 1; iteration <= config.iterations; iteration += 1) {
    // packRaptorInput проверяет, что карты доступа и выхода покрывают все
    // остановки; реальные карты на каждую пару подставляются в запрос ниже.
    const placeholderAccess = new Float64Array(stopIds.length);
    const placeholderEgress = new Float64Array(stopIds.length);
    const packed = packRaptorInput(
      input.network,
      input.periodId,
      placeholderAccess,
      placeholderEgress,
      input.departureMin,
      input.rangeWindowMin,
      input.maxTransfers,
      undefined,
      headwayFactors,
    );
    const patterns = packed.patterns;
    const patternRouteIds = patterns.map((pattern) => pattern.routeId);
    const patternSegmentOffsets = packed.input.routeSegmentOffsets;
    const penaltyBySegment = new Float64Array(packed.input.segmentTimes.length);
    // Штрафы адресованы по ключу (маршрут, откуда, куда) и разворачиваются в
    // индексы сегментов: воркер оперирует индексами, ключи знает вызывающий.
    for (let pattern = 0; pattern < patterns.length; pattern += 1) {
      const segmentStart = patternSegmentOffsets[pattern];
      for (let local = 0; local + 1 < patterns[pattern].stops.length; local += 1) {
        const fromId = stopIds[patterns[pattern].stops[local]];
        const toId = stopIds[patterns[pattern].stops[local + 1]];
        const penalty = segmentPenalties.get(SEGMENT_KEY(patternRouteIds[pattern], fromId, toId));
        if (penalty !== undefined) penaltyBySegment[segmentStart + local] = penalty;
      }
    }

    const routePenalties = new Float64Array(patterns.length);
    const bannedRoutes = new Uint8Array(patterns.length);
    const journeysPerPair: AssignmentJourney[][] = [];
    const accessWalk: number[] = [];
    const egressWalk: number[] = [];
    // Счётчик пар обнуляется на каждой итерации: он описывает текущий проход,
    // а не сумму по всем проходам.
    unroutedPairs = 0;

    input.pairs.forEach((pair) => {
      accessWalk.push(0);
      egressWalk.push(0);
      if (pair.tripsPerDay <= 0) {
        journeysPerPair.push([]);
        return;
      }
      const originIndex = zoneStops.get(pair.originZoneId);
      const destinationIndex = zoneStops.get(pair.destinationZoneId);
      if (originIndex === undefined || destinationIndex === undefined) {
        journeysPerPair.push([]);
        unroutedPairs += 1;
        return;
      }
      const originZone = zones.get(pair.originZoneId);
      const destinationZone = zones.get(pair.destinationZoneId);
      // Ходьба зона→остановка идёт в полезность поездки, а не в её время.
      accessWalk[accessWalk.length - 1] = walkMinutes(
        originZone, input.network.stops[originIndex].location, config.walkingSpeedKph,
      );
      egressWalk[egressWalk.length - 1] = walkMinutes(
        destinationZone, input.network.stops[destinationIndex].location, config.walkingSpeedKph,
      );

      const access = new Float64Array(stopIds.length).fill(Number.POSITIVE_INFINITY);
      const egress = new Float64Array(stopIds.length).fill(Number.POSITIVE_INFINITY);
      access[originIndex] = 0;
      egress[destinationIndex] = 0;

      const request: RaptorRequest = {
        type: "route",
        job: packed.input.job,
        stopCount: packed.input.stopCount,
        routeCount: packed.input.routeCount,
        maxTransfers: packed.input.maxTransfers,
        rangeWindowMin: packed.input.rangeWindowMin,
        accessTimeMin: access,
        egressTimeMin: egress,
        transferOffsets: packed.input.transferOffsets,
        transferTargets: packed.input.transferTargets,
        transferTimes: packed.input.transferTimes,
        routeOffsets: packed.input.routeOffsets,
        routeStopCounts: packed.input.routeStopCounts,
        routeStops: packed.input.routeStops,
        routeDepartureOffsets: packed.input.routeDepartureOffsets,
        departures: packed.input.departures,
        routeSegmentOffsets: packed.input.routeSegmentOffsets,
        segmentTimes: packed.input.segmentTimes,
        departureMin: packed.input.departureMin,
        walkWeight: packed.input.walkWeight,
        waitWeight: packed.input.waitWeight,
        shiftWeight: packed.input.shiftWeight,
        routePenalties,
        bannedRoutes,
        segmentPenalties: penaltyBySegment,
        maxAlternatives: config.maxTransitAlternatives,
        diversityPenaltyMin: config.alternativeDiversityPenaltyMin,
      };
      const { alternatives } = routeAlternatives(request);
      const journeys = alternatives.map((journey) => ({
        legs: journey.rides.flatMap((ride) => {
          const pattern = patterns[ride.pattern];
          const serviceId = serviceByRoute.get(pattern.routeId)?.[0] ?? "";
          return rideToLegs(ride, pattern, serviceId, runTimes, stopIds);
        }),
        transfers: journey.transfers,
        durationMin: journey.durationMin,
      }));
      if (journeys.length === 0) unroutedPairs += 1;
      journeysPerPair.push(journeys);
    });

    snapshot = assignOnce({
      pairs: input.pairs,
      journeysPerPair,
      accessWalkMin: accessWalk,
      egressWalkMin: egressWalk,
      zones,
      routeIds: input.network.routes.map((route) => route.id),
      stopIds,
      sectionCapacity,
      stopPlatformM: platformM,
      config,
      choice,
      segmentCrowdingPenalties: segmentPenalties,
      stopDwell: (stopId, boardings) => stopDwellFromCoefficients(dwell, stopId, boardings),
      totalTripsPerDay: totalTrips,
    });

    const serviceStopBoardings = new Map<string, number>();
    for (const [serviceId, stopId, value] of snapshot.serviceStopBoardings) {
      serviceStopBoardings.set(`${serviceId}|${stopId}`, value);
    }
    const targetPenalties = segmentCrowdingPenalties(snapshot.sectionLoads, config, runTimes);
    const penaltyDelta = maxFeedbackDelta(segmentPenalties, targetPenalties);
    segmentPenalties = blendFeedback(segmentPenalties, targetPenalties, config.damping, 0);

    const targetFactors = serviceHeadwayFactors(input.network, input.periodId, serviceStopBoardings);
    const factorDelta = maxFeedbackDelta(headwayFactors, targetFactors);
    headwayFactors = blendFeedback(headwayFactors, targetFactors, config.damping, 1);

    if (iteration > 1 && Math.max(penaltyDelta, factorDelta) <= config.convergenceTolerance) {
      convergedAfter = iteration;
      break;
    }
  }

  if (snapshot === null) throw new Error("Assignment produced no snapshot: iterations must be positive");
  const result = snapshot;
  return {
    type: "assigned",
    job: input.job,
    metrics: result.metrics,
    unserved: result.unserved,
    iterations: convergedAfter,
    maxLoadRatio: result.sectionLoads.reduce(
      (worst, section) => Math.max(worst, section.loadRatio), 0,
    ),
    lossReasons: result.lossReasons,
    serviceHeadwayFactors: [...headwayFactors.entries()].sort((a, b) => (
      a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0
    )),
    sectionLoads: result.sectionLoads.map((section) => ({
      routeId: section.routeId,
      fromStopId: section.fromStopId,
      toStopId: section.toStopId,
      passengers: section.passengers,
      capacity: section.capacity,
      loadRatio: section.loadRatio,
      crowdingLevel: section.crowdingLevel,
      deniedBoardings: section.deniedBoardings,
    })),
    stopFlows: result.stopFlows.map((stop) => ({
      stopId: stop.stopId,
      boardings: stop.boardings,
      alightings: stop.alightings,
      transfers: stop.transfers,
      dwellSeconds: stop.dwellSeconds,
      platformM: stop.platformM,
    })),
    routeFlows: result.routeFlows.map((route) => ({
      routeId: route.routeId,
      boardings: route.boardings,
      passengerSectionTraversals: route.passengerSectionTraversals,
    })),
    unroutedPairs,
  };
}
