import { fromLocalMeters, toLocalMeters } from "./projection";
import { buildGridIndex, gridNearest, type GridIndex } from "./spatial";
import type { NetworkPayload, TransitMode } from "./types";
import { routeWithRaptor, type RaptorRoutePattern } from "./workers/routing";

export const JOURNEY_SNAP_MAX_M = 500;
export const JOURNEY_CACHE_LIMIT = 32;

export interface JourneyLeg {
  mode: TransitMode;
  routeId: string;
  routeName: string;
  fromStopId: string;
  toStopId: string;
  departMin: number;
  arriveMin: number;
  stopIds: string[];
}

export interface JourneyPlan {
  found: boolean;
  originStopId: string;
  destinationStopId: string;
  originSnapM: number;
  destinationSnapM: number;
  legs: JourneyLeg[];
  stopIds: string[];
  coordinates: Array<[number, number]>;
  transfers: number;
  waitMin: number;
  walkToMin: number;
  walkFromMin: number;
  arriveMin: number;
  generalizedMin: number;
}

export interface JourneyRequest {
  network: NetworkPayload;
  periodId: string;
  origin: { lon: number; lat: number };
  destination: { lon: number; lat: number };
  /** Растёт при любом изменении сети, поэтому служит ключом кэша. */
  revision: number;
  departureMin?: number;
  rangeWindowMin?: number;
  maxTransfers?: number;
}

/**
 * Модель не умеет door-to-door: поездка начинается и заканчивается на
 * остановке (см. routing.py). Точка привязывается к ближайшей остановке в той
 * же локальной метрической системе, что и сеть, а слишком далёкая точка —
 * явная ошибка, а не тихая подмена.
 */
export function nearestStopIndex(
  network: NetworkPayload,
  point: { lon: number; lat: number },
  maxDistanceM: number = JOURNEY_SNAP_MAX_M,
): { index: number; distanceM: number } {
  const local = toLocalMeters(point.lon, point.lat, network.origin_lon, network.origin_lat);
  if (network.stops.length === 0) throw new Error("В сети нет остановок");
  const grid = stopGrid(network);
  const found = gridNearest(grid, local.x, local.y, maxDistanceM);
  if (!found) {
    const fallback = gridNearest(grid, local.x, local.y);
    const distanceM = fallback ? fallback.distance : Number.POSITIVE_INFINITY;
    throw new Error(
      `Ближайшая остановка в ${Math.round(distanceM)} м, допустимо не более ${maxDistanceM} м`,
    );
  }
  return { index: found.index, distanceM: found.distance };
}

const STOP_CELL_METERS = 250.0;
const stopGrids = new WeakMap<NetworkPayload["stops"], GridIndex>();

/** Grid over stop positions, cached per stop array. */
function stopGrid(network: NetworkPayload): GridIndex {
  const cached = stopGrids.get(network.stops);
  if (cached) return cached;
  const count = network.stops.length;
  const xs = new Float64Array(count);
  const ys = new Float64Array(count);
  for (let index = 0; index < count; index += 1) {
    xs[index] = network.stops[index].location.x;
    ys[index] = network.stops[index].location.y;
  }
  const grid = buildGridIndex(xs, ys, STOP_CELL_METERS);
  stopGrids.set(network.stops, grid);
  return grid;
}

function legStopIndices(
  pattern: RaptorRoutePattern,
  boardPosition: number,
  alightPosition: number,
): number[] {
  if (boardPosition === alightPosition) return [];
  const step = boardPosition < alightPosition ? 1 : -1;
  const indices: number[] = [];
  for (let position = boardPosition; ; position += step) {
    indices.push(pattern.stops[position]);
    if (position === alightPosition) break;
  }
  return indices;
}

function buildPlan(
  network: NetworkPayload,
  journey: Awaited<ReturnType<typeof routeWithRaptor>>["journey"],
  patterns: RaptorRoutePattern[],
  originStopId: string,
  destinationStopId: string,
  originSnapM: number,
  destinationSnapM: number,
): JourneyPlan {
  const originStop = network.stops.find((stop) => stop.id === originStopId);
  if (!originStop) throw new Error(`Остановка ${originStopId} исчезла из сети`);
  const originLonLat = fromLocalMeters(
    originStop.location.x,
    originStop.location.y,
    network.origin_lon,
    network.origin_lat,
  );
  const destinationStop = network.stops.find((stop) => stop.id === destinationStopId);
  if (!destinationStop) throw new Error(`Остановка ${destinationStopId} исчезла из сети`);

  const legs: JourneyLeg[] = [];
  const stopIds: string[] = [originStopId];
  const coordinates: Array<[number, number]> = [originLonLat];
  const pushCoordinate = (x: number, y: number) => {
    const last = coordinates[coordinates.length - 1];
    const point = fromLocalMeters(x, y, network.origin_lon, network.origin_lat);
    if (last && last[0] === point[0] && last[1] === point[1]) return;
    coordinates.push(point);
  };

  for (let leg = 0; leg < journey.routeIds.length; leg += 1) {
    const pattern = patterns[journey.routeIds[leg]];
    if (!pattern) {
      throw new Error(`rRAPTOR вернул неизвестный pattern ${journey.routeIds[leg]}`);
    }
    const boardPosition = pattern.stops.indexOf(journey.boardStops[leg]);
    const alightPosition = pattern.stops.indexOf(journey.alightStops[leg]);
    if (boardPosition < 0 || alightPosition < 0) {
      throw new Error("rRAPTOR вернул остановку, которой нет в маршруте");
    }
    const legStops = legStopIndices(pattern, boardPosition, alightPosition);
    if (legStops.length < 2) {
      throw new Error("Некорректная последовательность остановок в leg маршрута");
    }
    const legStopIds = legStops.map((index) => network.stops[index].id);
    for (const id of legStopIds) stopIds.push(id);
    for (const index of legStops) {
      const stop = network.stops[index];
      pushCoordinate(stop.location.x, stop.location.y);
    }
    const route = network.routes.find((item) => item.id === pattern.routeId);
    if (!route) throw new Error(`Маршрут ${pattern.routeId} отсутствует в сети`);
    legs.push({
      mode: route.mode,
      routeId: route.id,
      routeName: route.name,
      fromStopId: legStopIds[0],
      toStopId: legStopIds[legStopIds.length - 1],
      departMin: journey.departureMin[leg],
      arriveMin: journey.arrivalByLegMin[leg],
      stopIds: legStopIds,
    });
  }

  pushCoordinate(destinationStop.location.x, destinationStop.location.y);
  stopIds.push(destinationStopId);

  return {
    found: journey.found && legs.length > 0,
    originStopId,
    destinationStopId,
    originSnapM,
    destinationSnapM,
    legs,
    stopIds,
    coordinates,
    transfers: journey.transfers,
    waitMin: journey.waitMin,
    walkToMin: journey.walkToMin,
    walkFromMin: journey.walkFromMin,
    arriveMin: journey.arrivalMin,
    generalizedMin: journey.generalizedMin,
  };
}

const cache = new Map<string, JourneyPlan>();

export function clearJourneyCache(): void {
  cache.clear();
}

export async function planJourney(request: JourneyRequest): Promise<JourneyPlan> {
  const {
    network,
    periodId,
    origin,
    destination,
    revision,
    departureMin = 420,
    rangeWindowMin = 30,
    maxTransfers = 4,
  } = request;

  const originSnap = nearestStopIndex(network, origin);
  const destinationSnap = nearestStopIndex(network, destination);
  const originStopId = network.stops[originSnap.index].id;
  const destinationStopId = network.stops[destinationSnap.index].id;

  const key = [
    revision,
    periodId,
    originStopId,
    destinationStopId,
    departureMin,
    rangeWindowMin,
    maxTransfers,
  ].join("|");
  const cached = cache.get(key);
  if (cached) {
    // Свежие записи живут дольше: обновляем порядок вытеснения.
    cache.delete(key);
    cache.set(key, cached);
    return cached;
  }

  const { journey, patterns } = await routeWithRaptor(
    network,
    periodId,
    originSnap.index,
    destinationSnap.index,
    departureMin,
    rangeWindowMin,
    maxTransfers,
  );
  const plan = buildPlan(
    network,
    journey,
    patterns,
    originStopId,
    destinationStopId,
    originSnap.distanceM,
    destinationSnap.distanceM,
  );
  cache.set(key, plan);
  if (cache.size > JOURNEY_CACHE_LIMIT) {
    const oldest = cache.keys().next();
    if (!oldest.done) cache.delete(oldest.value);
  }
  return plan;
}

export function formatMinuteOfDay(minute: number): string {
  if (!Number.isFinite(minute)) return "—";
  const total = Math.round(minute);
  // 1440 — конец суток, а не полночь следующего дня: иначе период «вечер»
  // подписан 00:00 и выглядит как расписание на другой день.
  const hours = total === 1440 ? 24 : Math.floor(total / 60) % 24;
  return `${String(hours).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
}
