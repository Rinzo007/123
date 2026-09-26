import type { NetworkPayload } from "../types";

export type ReferenceEvaluationResult = {
  result: unknown;
  wallMs: number;
};

export type ReferenceDemandBatch = {
  counts: Uint8Array;
  costs: Float64Array;
  frequencies: Float64Array;
  rideBase: Float64Array;
  crowd: Float64Array;
  restWait: Float64Array;
  transfer: Float64Array;
};

export type ReferenceDemandOutput = {
  activeCounts: Uint8Array;
  order: Uint8Array;
  totals: Float64Array;
  computeMs: number;
};

export type ReferenceMatrixInput = {
  stops: number;
  offsets: Int32Array;
  targets: Int32Array;
  costs: Float64Array;
};

export type ReferenceMatrixOutput = {
  times: Float64Array;
  previous: Int32Array;
};

let referenceRequestId = 0;

export class ReferenceEvaluationClient {
  private readonly workers: Worker[] = [];
  private readonly tasks = new Map<number, { resolve: (value: ReferenceEvaluationResult) => void; reject: (error: Error) => void }>();
  private nextId = 1;
  private nextWorker = 0;
  private epoch = -1;

  constructor(workerCount = Math.max(1, Math.min(4, (navigator.hardwareConcurrency ?? 4) - 1))) {
    for (let index = 0; index < workerCount; index += 1) {
      const worker = new Worker("/assets/evaluation.worker-jRuvxHc_.js", { type: "module" });
      worker.onmessage = (event: MessageEvent) => {
        const data = event.data as { type?: string; id?: number; result?: unknown; wallMs?: number; detail?: string };
        if (data.type === "ready" || data.id == null) return;
        const task = this.tasks.get(data.id);
        if (!task) return;
        this.tasks.delete(data.id);
        if (data.type === "result") task.resolve({ result: data.result, wallMs: data.wallMs ?? 0 });
        else task.reject(new Error(data.detail ?? "reference evaluation worker failed"));
      };
      worker.onerror = (event) => {
        const error = new Error(event.message || "reference evaluation worker crashed");
        for (const task of this.tasks.values()) task.reject(error);
        this.tasks.clear();
      };
      this.workers.push(worker);
    }
  }

  init(epoch: number, demand: unknown, baselineT: unknown, layers: unknown, latitude: number): void {
    if (epoch === this.epoch) return;
    this.epoch = epoch;
    for (const worker of this.workers) {
      worker.postMessage({ type: "init", epoch, demand, baselineT, layers, latitude });
    }
  }

  run(lines: unknown, geoms: unknown, useBaseline = false, fare = 0): Promise<ReferenceEvaluationResult> {
    if (!this.workers.length) return Promise.reject(new Error("reference evaluation worker unavailable"));
    const id = this.nextId++;
    const worker = this.workers[this.nextWorker++ % this.workers.length];
    return new Promise((resolve, reject) => {
      this.tasks.set(id, { resolve, reject });
      worker.postMessage({ type: "run", id, epoch: this.epoch, lines, geoms, useBaseline, fare });
    });
  }

  cancel(): void {
    const before = this.nextId;
    for (const worker of this.workers) worker.postMessage({ type: "cancel", before });
    const error = new Error("reference evaluation superseded");
    for (const task of this.tasks.values()) task.reject(error);
    this.tasks.clear();
  }

  close(): void {
    this.cancel();
    for (const worker of this.workers) worker.terminate();
    this.workers.length = 0;
  }
}

export function solveReferenceDemand(batch: ReferenceDemandBatch): Promise<ReferenceDemandOutput> {
  return new Promise((resolve, reject) => {
    const worker = new Worker("/assets/demand-choice.worker-DAUlrAj6.js", { type: "module" });
    const id = `demand-${Date.now()}-${referenceRequestId++}`;
      worker.onmessage = (event: MessageEvent<{ id: string; output: Omit<ReferenceDemandOutput, "computeMs">; computeMs: number }>) => {
      if (event.data.id !== id) return;
      worker.terminate();
      resolve({ ...event.data.output, computeMs: event.data.computeMs });
    };
    worker.onerror = (event) => {
      worker.terminate();
      reject(new Error(event.message || "reference demand-choice worker failed"));
    };
    worker.postMessage({ id, batch }, [
      batch.counts.buffer,
      batch.costs.buffer,
      batch.frequencies.buffer,
      batch.rideBase.buffer,
      batch.crowd.buffer,
      batch.restWait.buffer,
      batch.transfer.buffer,
    ]);
  });
}

export async function solveReferenceMatrix(input: ReferenceMatrixInput): Promise<ReferenceMatrixOutput> {
  const workerCount = Math.max(1, Math.min(8, (navigator.hardwareConcurrency ?? 4) - 2, input.stops));
  const workers = Array.from({ length: workerCount }, () => new Worker("/assets/matrix.worker-E0A0h2Wn.js", { type: "module" }));
  const times = new Float64Array(input.stops * input.stops);
  times.fill(Number.POSITIVE_INFINITY);
  const previous = new Int32Array(input.stops * input.stops * 2);
  previous.fill(-1);
  const job = Date.now();
  await Promise.all(workers.map((worker, index) => new Promise<void>((resolve, reject) => {
    const start = Math.floor(index * input.stops / workerCount);
    const end = Math.floor((index + 1) * input.stops / workerCount);
    worker.onmessage = (event: MessageEvent) => {
      const data = event.data as { type?: string; job?: number; start?: number; times?: Float64Array; previous?: Int32Array };
      if (data.type !== "solved" || data.job !== job) return;
      if (data.times && data.start != null) times.set(data.times, data.start * input.stops);
      if (data.previous && data.start != null) previous.set(data.previous, data.start * input.stops * 2);
      worker.terminate();
      resolve();
    };
    worker.onerror = (event) => { worker.terminate(); reject(new Error(event.message || "reference matrix worker failed")); };
    worker.postMessage({
      type: "solve", job, start, end, stops: input.stops,
      offsets: input.offsets, targets: input.targets, costs: input.costs,
    });
  })));
  return { times, previous };
}

export function closeReferenceEvaluation(client: ReferenceEvaluationClient | null): void {
  client?.close();
}

export function networkForReference(network: NetworkPayload): NetworkPayload {
  return network;
}


export type ReferenceLine = {
  id: string;
  mode: "bus" | "tram" | "metro" | "rail";
  stops: Array<[number, number]>;
  headways: number[];
  row?: string;
  closed?: boolean;
};

export type ReferenceDemand = {
  pts: Array<[number, number, number, number]>;
  od: Array<[number, number, number, number]>;
  model?: Record<string, unknown>;
};

function fromLocalMeters(x: number, y: number, lon0: number, lat0: number): [number, number] {
  const earthRadius = 6378137;
  const cosLat = Math.cos((lat0 * Math.PI) / 180);
  return [
    lon0 + (x / Math.max(1e-9, earthRadius * cosLat)) * 180 / Math.PI,
    lat0 + (y / earthRadius) * 180 / Math.PI,
  ];
}

export function toReferenceDemand(network: NetworkPayload, trips = 1000): ReferenceDemand {
  const lon0 = network.origin_lon ?? 39.2;
  const lat0 = network.origin_lat ?? 51.67;
  const pts: Array<[number, number, number, number]> = network.stops.map((stop) => {
    const [lon, lat] = fromLocalMeters(stop.location.x, stop.location.y, lon0, lat0);
    return [lon, lat, 1, 1];
  });
  const last = Math.max(0, pts.length - 1);
  return {
    pts,
    od: pts.length >= 2 ? [[0, last, Math.max(0, trips), 0]] : [],
    model: {},
  };
}

export function toReferenceLines(network: NetworkPayload): ReferenceLine[] {
  const byId = new Map(network.stops.map((stop) => [stop.id, stop]));
  return network.routes.map((route) => {
    const service = network.services.find((item) => item.route_id === route.id);
    const stopCoords = route.stop_ids
      .map((id) => byId.get(id))
      .filter((stop): stop is NonNullable<typeof stop> => Boolean(stop))
      .map((stop) => fromLocalMeters(
        stop.location.x,
        stop.location.y,
        network.origin_lon ?? 39.2,
        network.origin_lat ?? 51.67,
      ));
    return {
      id: route.id,
      mode: route.mode,
      stops: stopCoords,
      headways: network.periods.map((period) => service?.headway_by_period[period.id] ?? 0),
    };
  });
}

export function toReferenceGeometries(network: NetworkPayload): Array<{
  stops: Array<[number, number]>;
  cum: number[];
}> {
  return toReferenceLines(network).map((line) => {
    const cum = [0];
    for (let i = 1; i < line.stops.length; i += 1) {
      const [lon1, lat1] = line.stops[i - 1];
      const [lon2, lat2] = line.stops[i];
      const dx = (lon2 - lon1) * 111320 * Math.cos((lat1 * Math.PI) / 180);
      const dy = (lat2 - lat1) * 111000;
      cum.push(cum[i - 1] + Math.hypot(dx, dy));
    }
    return { stops: line.stops, cum };
  });
}

export async function runReferencePreview(
  client: ReferenceEvaluationClient,
  network: NetworkPayload,
  trips = 1000,
): Promise<ReferenceEvaluationResult> {
  const demand = toReferenceDemand(network, trips);
  client.init(Date.now(), demand, undefined, [], network.origin_lat ?? 51.67);
  return client.run(toReferenceLines(network), toReferenceGeometries(network), false, 0);
}
