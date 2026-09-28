import { fromLocalMeters, toLocalMeters } from "./projection";
import { buildGridIndex, gridNearest, type GridIndex } from "./spatial";
import { assembleRouteCoordinates, type StreetGraph } from "./street-graph";
import {
  planStreetDoorAccess,
  streetWalkRoute,
  type StreetDoorAccess,
} from "./street-walk";
import type { NetworkPayload, TransitMode } from "./types";
import { routeWithRaptor, type RaptorChoiceWeights, type RaptorRoutePattern } from "./workers/routing";

export const JOURNEY_SNAP_MAX_M = 500;
export const JOURNEY_CACHE_LIMIT = 32;

export type JourneyLegMode = TransitMode | "walk";

export interface JourneyLeg {
  mode: JourneyLegMode;
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
  /** Boarding stop for door-to-door results, not the query point. */
  originStopId: string;
  /** Alighting stop for door-to-door results, not the query point. */
  destinationStopId: string;
  /** Query point to street-graph connector, in metres. */
  originSnapM: number;
  /** Query point to street-graph connector, in metres. */
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
  /** Street graph is mandatory: door-to-door planning has no stop fallback. */
  streetGraph: StreetGraph;
  periodId: string;
  origin: { lon: number; lat: number };
  destination: { lon: number; lat: number };
  /** Растёт при любом изменении сети, поэтому служит ключом кэша. */
  revision: number;
  departureMin?: number;
  rangeWindowMin?: number;
  maxTransfers?: number;
  walkingSpeedKph?: number;
  accessRadiusM?: number;
  /** Generalized-cost preset; default is Table 5 (JOURNEY_CHOICE_TABLE5). */
  choiceWeights?: RaptorChoiceWeights;
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
  door: StreetDoorAccess,
  journey: Awaited<ReturnType<typeof routeWithRaptor>>["journey"],
  patterns: RaptorRoutePattern[],
  origin: { lon: number; lat: number },
  destination: { lon: number; lat: number },
): JourneyPlan {
  const accessStop = journey.accessStop;
  const egressStop = journey.egressStop;
  if (
    accessStop < 0 ||
    egressStop < 0 ||
    accessStop >= network.stops.length ||
    egressStop >= network.stops.length ||
    !Number.isFinite(journey.walkToMin) ||
    !Number.isFinite(journey.walkFromMin)
  ) {
    throw new Error("rRAPTOR вернул недопустимую точку доступа или выхода");
  }

  const legs: JourneyLeg[] = [];
  const stopIds: string[] = [];
  const coordinates: Array<[number, number]> = [[origin.lon, origin.lat]];
  const pushLonLat = (lon: number, lat: number) => {
    const last = coordinates[coordinates.length - 1];
    if (last && last[0] === lon && last[1] === lat) return;
    coordinates.push([lon, lat]);
  };
  const pushStopCoordinate = (stopIndex: number) => {
    const stop = network.stops[stopIndex];
    const [lon, lat] = fromLocalMeters(
      stop.location.x,
      stop.location.y,
      network.origin_lon,
      network.origin_lat,
    );
    pushLonLat(lon, lat);
  };

  // Access: query point -> street path -> access-source stop.
  const accessNode = door.stopNodes[accessStop];
  if (accessNode < 0) throw new Error("Исходная остановка доступа не привязана к графу");
  const accessRoute = streetWalkRoute(door.csr, door.originTree, accessNode, true);
  for (const [lon, lat] of assembleRouteCoordinates(door.graph, accessRoute.nodes, accessRoute.edges)) {
    pushLonLat(lon, lat);
  }
  pushStopCoordinate(accessStop);
  const boardingStopId = network.stops[accessStop].id;
  stopIds.push(boardingStopId);

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
      pushStopCoordinate(index);
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

  // Egress: alighting stop -> street path -> query point.
  const egressNode = door.stopNodes[egressStop];
  if (egressNode < 0) throw new Error("Конечная остановка выхода не привязана к графу");
  const egressRoute = streetWalkRoute(door.csr, door.destinationTree, egressNode, false);
  for (const [lon, lat] of assembleRouteCoordinates(door.graph, egressRoute.nodes, egressRoute.edges)) {
    pushLonLat(lon, lat);
  }
  pushLonLat(destination.lon, destination.lat);
  const alightingStopId = network.stops[egressStop].id;
  stopIds.push(alightingStopId);

  if (legs.length > 0) {
    legs.unshift({
      mode: "walk",
      routeId: "walk",
      routeName: "Пешком",
      fromStopId: "origin",
      toStopId: boardingStopId,
      departMin: journey.departureMin[0] - journey.walkToMin,
      arriveMin: journey.departureMin[0],
      stopIds: ["origin", boardingStopId],
    });
    legs.push({
      mode: "walk",
      routeId: "walk",
      routeName: "Пешком",
      fromStopId: alightingStopId,
      toStopId: "destination",
      departMin: journey.arrivalByLegMin[journey.arrivalByLegMin.length - 1],
      arriveMin: journey.arrivalMin,
      stopIds: [alightingStopId, "destination"],
    });
  }

  return {
    // Без транзитных ног это чисто пешеходный результат: планировщику
    // транзита он не подходит, поэтому found=false, как и раньше при пустых legs.
    found: journey.found && legs.length > 2,
    originStopId: boardingStopId,
    destinationStopId: alightingStopId,
    originSnapM: door.originOffsetM,
    destinationSnapM: door.destinationOffsetM,
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

/** Cache is scoped per street-graph object: a new graph must never reuse walks. */
let journeyCaches = new WeakMap<object, Map<string, JourneyPlan>>();

function cacheFor(graph: StreetGraph): Map<string, JourneyPlan> {
  const cached = journeyCaches.get(graph);
  if (cached) return cached;
  const fresh = new Map<string, JourneyPlan>();
  journeyCaches.set(graph, fresh);
  return fresh;
}

export function clearJourneyCache(): void {
  journeyCaches = new WeakMap();
}

export async function planJourney(request: JourneyRequest): Promise<JourneyPlan> {
  const {
    network,
    streetGraph,
    periodId,
    origin,
    destination,
    revision,
    departureMin = 420,
    rangeWindowMin = 30,
    maxTransfers = 4,
    walkingSpeedKph,
    accessRadiusM,
    choiceWeights,
  } = request;
  if (!streetGraph) {
    throw new Error("Для маршрута door-to-door нужен уличный граф: тихого отката к остановкам нет");
  }

  const key = [
    revision,
    periodId,
    origin.lon.toFixed(6),
    origin.lat.toFixed(6),
    destination.lon.toFixed(6),
    destination.lat.toFixed(6),
    departureMin,
    rangeWindowMin,
    maxTransfers,
    walkingSpeedKph ?? "",
    accessRadiusM ?? "",
    choiceWeights ? `${choiceWeights.walk}|${choiceWeights.wait}|${choiceWeights.shift}` : "",
  ].join("|");
  const cache = cacheFor(streetGraph);
  const cached = cache.get(key);
  if (cached) {
    // Свежие записи живут дольше: обновляем порядок вытеснения.
    cache.delete(key);
    cache.set(key, cached);
    return cached;
  }

  // Уличные поиски — только при промахе кэша: это два Dijkstra на запрос.
  const door = planStreetDoorAccess({
    graph: streetGraph,
    network,
    origin,
    destination,
    walkingSpeedKph,
    accessRadiusM,
  });
  const { journey, patterns } = await routeWithRaptor(
    network,
    periodId,
    door.accessTimeMin,
    door.egressTimeMin,
    departureMin,
    rangeWindowMin,
    maxTransfers,
    choiceWeights,
  );
  const plan = buildPlan(network, door, journey, patterns, origin, destination);
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
