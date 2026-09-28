import { fromLocalMeters, toLocalMeters } from "./projection";
import { snapStreetPoint, type StreetGraph } from "./street-graph";
import type { NetworkPayload } from "./types";

/** Pedestrian speed, mirroring RouterConfig.walking_speed_kph. */
export const STREET_WALK_KPH = 5.0;
/** Shared pedestrian catchment, mirroring walk_transfer_radius_m. */
export const STREET_ACCESS_RADIUS_M = 500.0;
/** Maximum connector hop from a point or stop to the street graph. */
export const STREET_SNAP_MAX_M = 150.0;
/** TKST road class produced for motorways by _road_class_code. */
const MOTORWAY_STREET_CLASS = 4;
/** Closed-door budget comparison needs the same edge tolerance as Python. */
const METRE_TOLERANCE = 1e-9;

export interface StreetWalkCsr {
  nodeCount: number;
  offsets: Int32Array;
  targets: Int32Array;
  edges: Int32Array;
  reversed: Uint8Array;
  costMin: Float64Array;
}

export interface StreetWalkTree {
  root: number;
  minutes: Float64Array;
  previousNode: Int32Array;
  previousArc: Int32Array;
}

export interface StreetWalkRoute {
  nodes: Int32Array;
  edges: Int32Array;
  reversed: Uint8Array;
  costMin: number;
}

export interface StreetDoorAccess {
  graph: StreetGraph;
  csr: StreetWalkCsr;
  accessTimeMin: Float64Array;
  egressTimeMin: Float64Array;
  originNode: number;
  originOffsetM: number;
  destinationNode: number;
  destinationOffsetM: number;
  originTree: StreetWalkTree;
  destinationTree: StreetWalkTree;
  stopNodes: Int32Array;
  stopOffsetM: Float64Array;
}

export function streetWalkMinutes(lengthM: number, walkingSpeedKph = STREET_WALK_KPH): number {
  if (!Number.isFinite(walkingSpeedKph) || walkingSpeedKph <= 0) {
    throw new Error("Скорость пешехода должна быть больше нуля");
  }
  return Math.max(0, lengthM) / 1000 / walkingSpeedKph * 60;
}

/**
 * Walking CSR over directed car arcs. Vehicle one-way designations do not
 * constrain pedestrians, so every non-motorway arc is included in both
 * directions. Motorways keep their walking prohibition, matching road.py.
 */
export function buildStreetWalkCsr(
  graph: StreetGraph,
  walkingSpeedKph = STREET_WALK_KPH,
): StreetWalkCsr {
  streetWalkMinutes(0, walkingSpeedKph);
  const eligible: number[] = [];
  for (let edge = 0; edge < graph.edgeCount; edge += 1) {
    if (graph.edgeClass[edge] !== MOTORWAY_STREET_CLASS) eligible.push(edge);
  }

  const offsets = new Int32Array(graph.vertexCount + 1);
  for (const edge of eligible) {
    offsets[graph.edgeA[edge] + 1] += 1;
    offsets[graph.edgeB[edge] + 1] += 1;
  }
  for (let node = 1; node <= graph.vertexCount; node += 1) offsets[node] += offsets[node - 1];

  const arcs = eligible.length * 2;
  const targets = new Int32Array(arcs);
  const edges = new Int32Array(arcs);
  const reversed = new Uint8Array(arcs);
  const costMin = new Float64Array(arcs);
  const cursor = Int32Array.from(offsets.subarray(0, graph.vertexCount));
  // Forward records first, then reverse records: this preserves the same
  // outgoing-then-incoming visitation order as RoadGraph.walking_search.
  for (const edge of eligible) {
    const at = cursor[graph.edgeA[edge]]++;
    targets[at] = graph.edgeB[edge];
    edges[at] = edge;
    reversed[at] = 0;
    costMin[at] = streetWalkMinutes(graph.edgeLengthM[edge], walkingSpeedKph);
  }
  for (const edge of eligible) {
    const at = cursor[graph.edgeB[edge]]++;
    targets[at] = graph.edgeA[edge];
    edges[at] = edge;
    reversed[at] = 1;
    costMin[at] = streetWalkMinutes(graph.edgeLengthM[edge], walkingSpeedKph);
  }

  return { nodeCount: graph.vertexCount, offsets, targets, edges, reversed, costMin };
}

function localDistance(ax: number, ay: number, bx: number, by: number): number {
  return Math.sqrt((ax - bx) * (ax - bx) + (ay - by) * (ay - by));
}

function pushHeap(
  times: number[],
  serials: number[],
  nodes: number[],
  time: number,
  serial: number,
  node: number,
): void {
  times.push(time);
  serials.push(serial);
  nodes.push(node);
  let child = times.length - 1;
  while (child > 0) {
    const parent = (child - 1) >> 1;
    if (
      times[child] > times[parent] ||
      (times[child] === times[parent] && serials[child] >= serials[parent])
    ) {
      break;
    }
    for (const values of [times, serials, nodes]) {
      const held = values[child];
      values[child] = values[parent];
      values[parent] = held;
    }
    child = parent;
  }
}

function popHeap(times: number[], serials: number[], nodes: number[]): [number, number, number] {
  const time = times[0];
  const serial = serials[0];
  const node = nodes[0];
  const lastTime = times.pop()!;
  const lastSerial = serials.pop()!;
  const lastNode = nodes.pop()!;
  if (times.length > 0) {
    times[0] = lastTime;
    serials[0] = lastSerial;
    nodes[0] = lastNode;
    let parent = 0;
    for (;;) {
      const left = parent * 2 + 1;
      const right = left + 1;
      let selected = parent;
      for (const child of [left, right]) {
        if (child >= times.length) continue;
        if (
          times[child] < times[selected] ||
          (times[child] === times[selected] && serials[child] < serials[selected])
        ) {
          selected = child;
        }
      }
      if (selected === parent) break;
      for (const values of [times, serials, nodes]) {
        const held = values[parent];
        values[parent] = values[selected];
        values[selected] = held;
      }
      parent = selected;
    }
  }
  return [time, serial, node];
}

/**
 * One all-source walking search. Only target nodes and the radius budget are
 * needed later, so the search stops once every settled target is known or the
 * heap leaves the budget.
 */
export function searchStreetWalk(
  csr: StreetWalkCsr,
  root: number,
  targets: Int32Array | number[],
  maxMinutes = Number.POSITIVE_INFINITY,
): StreetWalkTree {
  if (!Number.isInteger(root) || root < 0 || root >= csr.nodeCount) {
    throw new Error("Начальный узел пешеходного поиска вне графа");
  }
  if (!(maxMinutes >= 0)) throw new Error("Бюджет пешеходного поиска должен быть неотрицательным");
  const uniqueTargets = new Set<number>();
  for (const target of targets) {
    if (!Number.isInteger(target) || target < 0 || target >= csr.nodeCount) {
      throw new Error("Целевой узел пешеходного поиска вне графа");
    }
    uniqueTargets.add(target);
  }

  const minutes = new Float64Array(csr.nodeCount).fill(Number.POSITIVE_INFINITY);
  const previousNode = new Int32Array(csr.nodeCount).fill(-1);
  const previousArc = new Int32Array(csr.nodeCount).fill(-1);
  const times: number[] = [];
  const serials: number[] = [];
  const nodes: number[] = [];
  let serial = 1;
  minutes[root] = 0;
  pushHeap(times, serials, nodes, 0, 0, root);

  while (times.length > 0) {
    const [duration, , node] = popHeap(times, serials, nodes);
    if (duration !== minutes[node]) continue;
    if (duration > maxMinutes) break;
    if (uniqueTargets.has(node)) {
      uniqueTargets.delete(node);
      if (uniqueTargets.size === 0) break;
    }
    for (let arc = csr.offsets[node]; arc < csr.offsets[node + 1]; arc += 1) {
      const neighbor = csr.targets[arc];
      const candidate = duration + csr.costMin[arc];
      if (candidate < minutes[neighbor]) {
        minutes[neighbor] = candidate;
        previousNode[neighbor] = node;
        previousArc[neighbor] = arc;
        pushHeap(times, serials, nodes, candidate, serial, neighbor);
        serial += 1;
      }
    }
  }

  return { root, minutes, previousNode, previousArc };
}

/** Operational walk route between the search root and a settled target. */
export function streetWalkRoute(
  csr: StreetWalkCsr,
  tree: StreetWalkTree,
  target: number,
  rootFirst: boolean,
): StreetWalkRoute {
  if (!Number.isInteger(target) || target < 0 || target >= csr.nodeCount) {
    throw new Error("Целевой узел пешеходного маршрута вне графа");
  }
  if (!Number.isFinite(tree.minutes[target])) {
    throw new Error(`Узел ${target} недостижим пешком`);
  }
  // Цепочка target -> ... -> root. Записанная дуга всегда идёт от nearer к
  // current, поэтому при движении к корню каждая дуга проходится задом
  // наперёд относительно своего направления в CSR.
  const arcs: number[] = [];
  const chain: number[] = [target];
  let current = target;
  while (current !== tree.root) {
    const nearer = tree.previousNode[current];
    const arc = tree.previousArc[current];
    if (nearer < 0 || arc < 0) throw new Error("Пешеходный маршрут не восстанавливается");
    arcs.push(arc);
    chain.push(nearer);
    current = nearer;
  }
  if (rootFirst) {
    arcs.reverse();
    chain.reverse();
  }
  const edges = new Int32Array(arcs.length);
  const reversed = new Uint8Array(arcs.length);
  const nodes = Int32Array.from(chain);
  let costMin = 0;
  for (let position = 0; position < arcs.length; position += 1) {
    const arc = arcs[position];
    edges[position] = csr.edges[arc];
    // При движении к корню — против направления дуги, от корня — по нему.
    reversed[position] = (rootFirst ? csr.reversed[arc] : csr.reversed[arc] ? 0 : 1) as 0 | 1;
    costMin += csr.costMin[arc];
  }
  return { nodes, edges, reversed, costMin };
}

export interface StreetDoorRequest {
  graph: StreetGraph;
  network: NetworkPayload;
  origin: { lon: number; lat: number };
  destination: { lon: number; lat: number };
  walkingSpeedKph?: number;
  accessRadiusM?: number;
  snapMaxM?: number;
}

/**
 * Street-graph access/egress for every transit stop.
 *
 * Query points and stops first snap to graph nodes; graph minutes plus both
 * straight connector hops must fit the shared pedestrian catchment. There is
 * no Euclidean fallback for disconnected pairs.
 */
export function planStreetDoorAccess(request: StreetDoorRequest): StreetDoorAccess {
  const {
    graph,
    network,
    origin,
    destination,
    walkingSpeedKph = STREET_WALK_KPH,
    accessRadiusM = STREET_ACCESS_RADIUS_M,
    snapMaxM = STREET_SNAP_MAX_M,
  } = request;
  if (!Number.isFinite(accessRadiusM) || accessRadiusM < 0) {
    throw new Error("Радиус пешеходной доступности должен быть неотрицательным");
  }
  if (!(snapMaxM >= 0)) throw new Error("Порог привязки к графу должен быть неотрицательным");

  const originSnap = snapStreetPoint(graph, origin.lon, origin.lat, snapMaxM);
  if (!originSnap) {
    throw new Error(`Начальная точка дальше ${snapMaxM} м от уличного графа`);
  }
  const destinationSnap = snapStreetPoint(graph, destination.lon, destination.lat, snapMaxM);
  if (!destinationSnap) {
    throw new Error(`Конечная точка дальше ${snapMaxM} м от уличного графа`);
  }

  const originLocal = toLocalMeters(origin.lon, origin.lat, network.origin_lon, network.origin_lat);
  const destinationLocal = toLocalMeters(
    destination.lon,
    destination.lat,
    network.origin_lon,
    network.origin_lat,
  );
  const originNodeLocal = toLocalMeters(
    graph.lon[originSnap.node] / 1e6,
    graph.lat[originSnap.node] / 1e6,
    network.origin_lon,
    network.origin_lat,
  );
  const destinationNodeLocal = toLocalMeters(
    graph.lon[destinationSnap.node] / 1e6,
    graph.lat[destinationSnap.node] / 1e6,
    network.origin_lon,
    network.origin_lat,
  );
  const originOffsetM = localDistance(
    originLocal.x,
    originLocal.y,
    originNodeLocal.x,
    originNodeLocal.y,
  );
  const destinationOffsetM = localDistance(
    destinationLocal.x,
    destinationLocal.y,
    destinationNodeLocal.x,
    destinationNodeLocal.y,
  );

  const stopNodes = new Int32Array(network.stops.length).fill(-1);
  const stopOffsetM = new Float64Array(network.stops.length).fill(Number.POSITIVE_INFINITY);
  for (let stop = 0; stop < network.stops.length; stop += 1) {
    const [stopLon, stopLat] = fromLocalMeters(
      network.stops[stop].location.x,
      network.stops[stop].location.y,
      network.origin_lon,
      network.origin_lat,
    );
    const snapped = snapStreetPoint(graph, stopLon, stopLat, snapMaxM);
    if (!snapped) continue;
    const nodeLocal = toLocalMeters(
      graph.lon[snapped.node] / 1e6,
      graph.lat[snapped.node] / 1e6,
      network.origin_lon,
      network.origin_lat,
    );
    stopNodes[stop] = snapped.node;
    stopOffsetM[stop] = localDistance(
      network.stops[stop].location.x,
      network.stops[stop].location.y,
      nodeLocal.x,
      nodeLocal.y,
    );
  }

  const csr = buildStreetWalkCsr(graph, walkingSpeedKph);
  const budgetMin = (accessRadiusM / 1000 / walkingSpeedKph) * 60;
  const wanted = stopNodes.filter((node) => node >= 0);
  const originTree = searchStreetWalk(csr, originSnap.node, wanted, budgetMin);
  const destinationTree = searchStreetWalk(csr, destinationSnap.node, wanted, budgetMin);
  const accessTimeMin = new Float64Array(network.stops.length).fill(Number.POSITIVE_INFINITY);
  const egressTimeMin = new Float64Array(network.stops.length).fill(Number.POSITIVE_INFINITY);
  for (let stop = 0; stop < network.stops.length; stop += 1) {
    const node = stopNodes[stop];
    if (node < 0) continue;
    const accessGraphM = originTree.minutes[node] * (walkingSpeedKph / 60) * 1000;
    const accessM = originOffsetM + accessGraphM + stopOffsetM[stop];
    if (Number.isFinite(accessM) && accessM <= accessRadiusM + METRE_TOLERANCE) {
      accessTimeMin[stop] = streetWalkMinutes(accessM, walkingSpeedKph);
    }
    const egressGraphM = destinationTree.minutes[node] * (walkingSpeedKph / 60) * 1000;
    const egressM = stopOffsetM[stop] + egressGraphM + destinationOffsetM;
    if (Number.isFinite(egressM) && egressM <= accessRadiusM + METRE_TOLERANCE) {
      egressTimeMin[stop] = streetWalkMinutes(egressM, walkingSpeedKph);
    }
  }

  if (!accessTimeMin.some((value) => Number.isFinite(value))) {
    throw new Error(`Ни одна остановка недоступна пешком в пределах ${accessRadiusM} м от начала`);
  }
  if (!egressTimeMin.some((value) => Number.isFinite(value))) {
    throw new Error(`Ни одна остановка недоступна пешком в пределах ${accessRadiusM} м до конца`);
  }
  return {
    graph,
    csr,
    accessTimeMin,
    egressTimeMin,
    originNode: originSnap.node,
    originOffsetM,
    destinationNode: destinationSnap.node,
    destinationOffsetM,
    originTree,
    destinationTree,
    stopNodes,
    stopOffsetM,
  };
}
