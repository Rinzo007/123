const STREETS_MAGIC = 0x54534b54; // "TKST" little-endian
export const STREETS_VERSION = 2;
const HEADER_BYTES = 24;

export interface StreetGraph {
  vertexCount: number;
  edgeCount: number;
  lon: Int32Array;
  lat: Int32Array;
  component: Int32Array;
  edgeA: Int32Array;
  edgeB: Int32Array;
  edgeLengthM: Int32Array;
  edgeClass: Uint8Array;
  edgeSpeedKph: Uint8Array;
  edgeDirection: Uint8Array;
  geomOffset: Int32Array;
  geomDeltaX: Int32Array;
  geomDeltaY: Int32Array;
  names: string[];
}

export function decodeStreets(buffer: ArrayBuffer): StreetGraph {
  if (buffer.byteLength < HEADER_BYTES) {
    throw new Error("Некорректный streets.bin: короткий буфер");
  }
  const view = new DataView(buffer);
  const magic = view.getUint32(0, true);
  const version = view.getUint32(4, true);
  if (magic !== STREETS_MAGIC) throw new Error("Некорректный streets.bin: magic");
  if (version !== STREETS_VERSION) {
    throw new Error(`Неподдерживаемая версия streets.bin: ${version}`);
  }

  const vertexCount = view.getUint32(8, true);
  const edgeCount = view.getUint32(12, true);
  const flatCount = view.getUint32(16, true);
  const namesLength = view.getUint32(20, true);

  let cursor = HEADER_BYTES;
  const lon = new Int32Array(vertexCount);
  const lat = new Int32Array(vertexCount);
  for (let index = 0; index < vertexCount; index += 1) {
    lon[index] = view.getInt32(cursor, true);
    lat[index] = view.getInt32(cursor + 4, true);
    cursor += 8;
  }

  const component = new Int32Array(buffer.slice(cursor, cursor + vertexCount * 4));
  cursor += vertexCount * 4;

  const edgeA = new Int32Array(buffer.slice(cursor, cursor + edgeCount * 4));
  cursor += edgeCount * 4;
  const edgeB = new Int32Array(buffer.slice(cursor, cursor + edgeCount * 4));
  cursor += edgeCount * 4;
  const edgeLengthM = new Int32Array(buffer.slice(cursor, cursor + edgeCount * 4));
  cursor += edgeCount * 4;
  // edge_name is only a label lookup, not needed for routing.
  cursor += edgeCount * 4;

  const geomOffset = new Int32Array(buffer.slice(cursor, cursor + (edgeCount + 1) * 4));
  cursor += (edgeCount + 1) * 4;

  const edgeClass = new Uint8Array(buffer.slice(cursor, cursor + edgeCount));
  cursor += edgeCount;
  const edgeSpeedKph = new Uint8Array(buffer.slice(cursor, cursor + edgeCount));
  cursor += edgeCount;
  const edgeDirection = new Uint8Array(buffer.slice(cursor, cursor + edgeCount));
  cursor += edgeCount;
  while (cursor % 4 !== 0) cursor += 1;

  const flatBytes = new Int32Array(buffer.slice(cursor, cursor + flatCount * 2));
  const geomDeltaX = new Int32Array(flatCount);
  const geomDeltaY = new Int32Array(flatCount);
  for (let index = 0; index < flatCount; index += 1) {
    geomDeltaX[index] = flatBytes[index * 2];
    geomDeltaY[index] = flatBytes[index * 2 + 1];
  }

  let names: string[] = [];
  if (namesLength > 0) {
    const text = new TextDecoder().decode(buffer.slice(buffer.byteLength - namesLength));
    names = (JSON.parse(text) as { names: string[] }).names;
  }

  return {
    vertexCount,
    edgeCount,
    lon,
    lat,
    component,
    edgeA,
    edgeB,
    edgeLengthM,
    edgeClass,
    edgeSpeedKph,
    edgeDirection,
    geomOffset,
    geomDeltaX,
    geomDeltaY,
    names,
  };
}

export interface StreetCsr {
  offsets: Int32Array;
  targets: Int32Array;
  edges: Int32Array;
  costMin: Float64Array;
}

export function buildStreetCsr(graph: StreetGraph): StreetCsr {
  for (let edge = 0; edge < graph.edgeCount; edge += 1) {
    if (graph.edgeSpeedKph[edge] === 0) {
      throw new Error(`У ребра улицы #${edge} не задана скорость`);
    }
  }

  const degree = new Int32Array(graph.vertexCount + 1);
  for (let edge = 0; edge < graph.edgeCount; edge += 1) {
    degree[graph.edgeA[edge] + 1] += 1;
  }
  for (let index = 1; index <= graph.vertexCount; index += 1) degree[index] += degree[index - 1];
  const total = degree[graph.vertexCount];
  const offsets = new Int32Array(degree.subarray(0, graph.vertexCount + 1));

  const targets = new Int32Array(total);
  const edges = new Int32Array(total);
  const costMin = new Float64Array(total);
  const cursor = Int32Array.from(offsets.subarray(0, graph.vertexCount));

  // RoadEdge rows are already directed, so every row becomes exactly one arc.
  for (let edge = 0; edge < graph.edgeCount; edge += 1) {
    const from = graph.edgeA[edge];
    const at = cursor[from]++;
    targets[at] = graph.edgeB[edge];
    edges[at] = edge;
    costMin[at] = (graph.edgeLengthM[edge] / 1000.0 / graph.edgeSpeedKph[edge]) * 60.0;
  }

  return { offsets, targets, edges, costMin };
}

export interface StreetRouteRequest {
  start: number;
  end: number;
}

export interface StreetRouteResult {
  edges: Int32Array;
  nodes: Int32Array;
  costMin: number;
}

export type StreetWorkerMessage =
  | {
      type: "route";
      job: number;
      requests: Int32Array;
      offsets: Int32Array;
      targets: Int32Array;
      edges: Int32Array;
      costMin: Float64Array;
      component: Int32Array;
    }
  | { type: "cancel"; before: number };

export type StreetWorkerReply =
  | {
      type: "routed";
      job: number;
      results: Array<{ edges: Int32Array; nodes: Int32Array; costMin: number }>;
    }
  | { type: "failed"; job: number; detail: string };

let requestId = 0;

export function routeStreets(
  graph: StreetGraph,
  requests: StreetRouteRequest[],
): Promise<StreetRouteResult[]> {
  for (const request of requests) {
    if (
      !Number.isInteger(request.start) ||
      !Number.isInteger(request.end) ||
      request.start < 0 ||
      request.end < 0 ||
      request.start >= graph.vertexCount ||
      request.end >= graph.vertexCount
    ) {
      throw new Error("Указанный узел улицы вне графа");
    }
    if (graph.component[request.start] !== graph.component[request.end]) {
      return Promise.reject(
        new Error(
          "Route points lie in disconnected road graph components; " +
            "the street network is missing a connecting segment between them",
        ),
      );
    }
  }

  const csr = buildStreetCsr(graph);
  const packed = new Int32Array(requests.length * 2);
  requests.forEach((request, index) => {
    packed[index * 2] = request.start;
    packed[index * 2 + 1] = request.end;
  });

  const worker = new Worker(new URL("./workers/street.worker.ts", import.meta.url), {
    type: "module",
  });
  const job = ++requestId;
  return new Promise((resolve, reject) => {
    worker.onmessage = (event: MessageEvent<StreetWorkerReply>) => {
      if (event.data.job !== job) return;
      worker.terminate();
      if (event.data.type === "failed") {
        reject(new Error(event.data.detail));
        return;
      }
      resolve(
        event.data.results.map((item) => ({
          edges: item.edges,
          nodes: item.nodes,
          costMin: item.costMin,
        })),
      );
    };
    worker.onerror = (event) => {
      worker.terminate();
      reject(new Error(event.message || "street routing worker failed"));
    };
    const component = graph.component.slice();
    worker.postMessage(
      {
        type: "route",
        job,
        requests: packed,
        offsets: csr.offsets,
        targets: csr.targets,
        edges: csr.edges,
        costMin: csr.costMin,
        component,
      },
      {
        transfer: [
          packed.buffer,
          csr.offsets.buffer,
          csr.targets.buffer,
          csr.edges.buffer,
          csr.costMin.buffer,
          component.buffer,
        ],
      },
    );
  });
}

export function edgePolyline(
  graph: StreetGraph,
  edge: number,
  reversed = false,
): Array<[number, number]> {
  const start = graph.geomOffset[edge];
  const end = graph.geomOffset[edge + 1];
  const points: Array<[number, number]> = [[graph.lon[graph.edgeA[edge]], graph.lat[graph.edgeA[edge]]]];
  let x = points[0][0];
  let y = points[0][1];
  for (let index = start; index < end; index += 1) {
    x += graph.geomDeltaX[index];
    y += graph.geomDeltaY[index];
    points.push([x, y]);
  }
  return reversed ? points.reverse() : points;
}

export function assembleRouteCoordinates(
  graph: StreetGraph,
  nodes: Int32Array,
  edges: Int32Array,
): Array<[number, number]> {
  const out: Array<[number, number]> = [];
  for (let index = 0; index + 1 < nodes.length && index < edges.length; index += 1) {
    const edge = edges[index];
    const reversed = graph.edgeA[edge] === nodes[index + 1];
    for (const [x, y] of edgePolyline(graph, edge, reversed)) {
      const last = out[out.length - 1];
      if (last && last[0] === x && last[1] === y) continue;
      out.push([x, y]);
    }
  }
  return out.map(([x, y]) => [x / 1e6, y / 1e6] as [number, number]);
}

const SNAP_METERS = 150.0;

export function nearestGraphNode(
  graph: StreetGraph,
  lon: number,
  lat: number,
): number | null {
  const microLon = Math.round(lon * 1e6);
  const microLat = Math.round(lat * 1e6);
  const cosLat = Math.cos((lat * Math.PI) / 180);
  let best = -1;
  let bestMeters = Infinity;
  for (let index = 0; index < graph.vertexCount; index += 1) {
    const dx = ((graph.lon[index] - microLon) * 111320 * cosLat) / 1e6;
    const dy = ((graph.lat[index] - microLat) * 110574) / 1e6;
    const meters = Math.hypot(dx, dy);
    if (meters < bestMeters) {
      bestMeters = meters;
      best = index;
    }
  }
  if (best < 0 || bestMeters > SNAP_METERS) return null;
  return best;
}
