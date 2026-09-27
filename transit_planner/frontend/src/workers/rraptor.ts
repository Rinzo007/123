import type { NetworkPayload, TransitMode } from "../types";

const MODE_SPEED_KPH: Record<TransitMode, number> = {
  bus: 18,
  tram: 19,
  metro: 70,
  rail: 58,
};

export interface RaptorJourney {
  found: boolean;
  arrivalMin: number;
  generalizedMin: number;
  transfers: number;
  routeIds: Int32Array;
  boardStops: Int32Array;
  alightStops: Int32Array;
  departureMin: Float64Array;
  arrivalByLegMin: Float64Array;
  walkToMin: number;
  walkFromMin: number;
  waitMin: number;
  departureShiftMin: number;
}

type RaptorRequest = {
  type: "route";
  job: number;
  stopCount: number;
  routeCount: number;
  maxTransfers: number;
  rangeWindowMin: number;
  accessTimeMin: Float64Array;
  egressTimeMin: Float64Array;
  transferOffsets: Int32Array;
  transferTargets: Int32Array;
  transferTimes: Float64Array;
  routeOffsets: Int32Array;
  routeStopCounts: Int32Array;
  routeStops: Int32Array;
  routeDepartureOffsets: Int32Array;
  departures: Float64Array;
  routeSegmentOffsets: Int32Array;
  segmentTimes: Float64Array;
  origin: number;
  destination: number;
  departureMin: number;
};

interface Pattern {
  stops: number[];
  departures: number[];
  segmentTimes: number[];
}

let requestId = 0;

function distanceMin(a: { x: number; y: number }, b: { x: number; y: number }, speedKph = 5): number {
  return Math.hypot(b.x - a.x, b.y - a.y) / 1000 / speedKph * 60;
}

function routePointsLength(
  network: NetworkPayload,
  route: NetworkPayload["routes"][number],
  index: number,
): number {
  const ids = route.stop_ids;
  const stops = new Map(network.stops.map((stop) => [stop.id, stop]));
  const from = stops.get(ids[index]);
  const to = stops.get(ids[index + 1]);
  if (!from || !to) return 0;
  return distanceMin(from.location, to.location, 1000) * 1000;
}

function segmentTime(
  network: NetworkPayload,
  route: NetworkPayload["routes"][number],
  index: number,
): number {
  const ids = route.stop_ids;
  const stops = new Map(network.stops.map((stop) => [stop.id, stop]));
  const from = stops.get(ids[index]);
  const to = stops.get(ids[index + 1]);
  if (!from || !to) return 0;
  const distanceKm = Math.hypot(to.location.x - from.location.x, to.location.y - from.location.y) / 1000;
  return distanceKm / MODE_SPEED_KPH[route.mode] * 60;
}

function buildPatterns(
  network: NetworkPayload,
  periodId: string,
): Pattern[] {
  const period = network.periods.find((item) => item.id === periodId);
  if (!period) return [];
  const patterns: Pattern[] = [];
  const stopIndex = new Map(network.stops.map((stop, index) => [stop.id, index]));

  for (const service of network.services) {
    const headway = service.headway_by_period[periodId];
    if (!headway || headway <= 0) continue;
    const route = network.routes.find((item) => item.id === service.route_id);
    if (!route || route.stop_ids.length < 2) continue;

    const baseStops = route.stop_ids
      .map((id) => stopIndex.get(id))
      .filter((id): id is number => id !== undefined);
    if (baseStops.length < 2) continue;

    const baseSegmentTimes = baseStops.slice(0, -1).map((_, index) => segmentTime(network, route, index));
    const departures: number[] = [];
    for (let departure = period.start_minute; departure < period.end_minute; departure += headway) {
      departures.push(departure);
    }
    patterns.push({ stops: baseStops, departures, segmentTimes: baseSegmentTimes });

    if (route.both_ways !== false) {
      patterns.push({
        stops: [...baseStops].reverse(),
        departures: [...departures],
        segmentTimes: [...baseSegmentTimes].reverse(),
      });
    }
  }
  return patterns;
}

function pack(
  network: NetworkPayload,
  periodId: string,
  origin: number,
  destination: number,
  departureMin: number,
  rangeWindowMin: number,
  maxTransfers: number,
): RaptorRequest {
  const stops = network.stops;
  const patterns = buildPatterns(network, periodId);
  const accessTimeMin = new Float64Array(stops.length);
  const egressTimeMin = new Float64Array(stops.length);
  accessTimeMin.fill(Number.POSITIVE_INFINITY);
  egressTimeMin.fill(Number.POSITIVE_INFINITY);

  for (let i = 0; i < stops.length; i += 1) {
    accessTimeMin[i] = i === origin ? 0 : Number.POSITIVE_INFINITY;
    egressTimeMin[i] = i === destination ? 0 : Number.POSITIVE_INFINITY;
  }

  const transferPairs: Array<[number, number, number]> = [];
  const transferRadiusM = 500;
  for (let i = 0; i < stops.length; i += 1) {
    for (let j = i + 1; j < stops.length; j += 1) {
      const minutes = distanceMin(stops[i].location, stops[j].location);
      if (minutes * 5000 <= transferRadiusM) {
        transferPairs.push([i, j, minutes]);
        transferPairs.push([j, i, minutes]);
      }
    }
  }

  const transferOffsets = new Int32Array(stops.length + 1);
  for (const [from] of transferPairs) transferOffsets[from + 1] += 1;
  for (let i = 0; i < stops.length; i += 1) transferOffsets[i + 1] += transferOffsets[i];
  const transferCursor = transferOffsets.slice(0, stops.length);
  const transferTargets = new Int32Array(transferPairs.length);
  const transferTimes = new Float64Array(transferPairs.length);
  for (const [from, to, time] of transferPairs) {
    const index = transferCursor[from]++;
    transferTargets[index] = to;
    transferTimes[index] = time;
  }

  const routeOffsets = new Int32Array(patterns.length + 1);
  const routeStopCounts = new Int32Array(patterns.length);
  const routeDepartureOffsets = new Int32Array(patterns.length + 1);
  const routeSegmentOffsets = new Int32Array(patterns.length + 1);
  let stopTotal = 0;
  let departureTotal = 0;
  let segmentTotal = 0;
  for (let i = 0; i < patterns.length; i += 1) {
    routeOffsets[i] = stopTotal;
    routeStopCounts[i] = patterns[i].stops.length;
    stopTotal += patterns[i].stops.length;
    routeDepartureOffsets[i] = departureTotal;
    departureTotal += patterns[i].departures.length;
    routeSegmentOffsets[i] = segmentTotal;
    segmentTotal += patterns[i].segmentTimes.length;
  }
  routeOffsets[patterns.length] = stopTotal;
  routeDepartureOffsets[patterns.length] = departureTotal;
  routeSegmentOffsets[patterns.length] = segmentTotal;

  const routeStops = new Int32Array(stopTotal);
  const departures = new Float64Array(departureTotal);
  const segmentTimes = new Float64Array(segmentTotal);
  let stopCursor = 0;
  let departureCursor = 0;
  let segmentCursor = 0;
  for (const pattern of patterns) {
    routeStops.set(pattern.stops, stopCursor);
    departures.set(pattern.departures, departureCursor);
    segmentTimes.set(pattern.segmentTimes, segmentCursor);
    stopCursor += pattern.stops.length;
    departureCursor += pattern.departures.length;
    segmentCursor += pattern.segmentTimes.length;
  }

  return {
    type: "route",
    job: ++requestId,
    stopCount: stops.length,
    routeCount: patterns.length,
    maxTransfers,
    rangeWindowMin,
    accessTimeMin,
    egressTimeMin,
    transferOffsets,
    transferTargets,
    transferTimes,
    routeOffsets,
    routeStopCounts,
    routeStops,
    routeDepartureOffsets,
    departures,
    routeSegmentOffsets,
    segmentTimes,
    origin,
    destination,
    departureMin,
  };
}

export function routeWithRaptor(
  network: NetworkPayload,
  periodId: string,
  origin: number,
  destination: number,
  departureMin = 420,
  rangeWindowMin = 30,
  maxTransfers = 4,
): Promise<RaptorJourney> {
  if (origin < 0 || origin >= network.stops.length) {
    return Promise.reject(new Error("RAPTOR origin outside stop range"));
  }
  if (destination < 0 || destination >= network.stops.length) {
    return Promise.reject(new Error("RAPTOR destination outside stop range"));
  }

  const worker = new Worker(new URL("./rraptor.worker.ts", import.meta.url), { type: "module" });
  const input = pack(
    network,
    periodId,
    origin,
    destination,
    departureMin,
    rangeWindowMin,
    maxTransfers,
  );

  return new Promise((resolve, reject) => {
    worker.onmessage = (event: MessageEvent<{ type?: string; job?: number; result?: RaptorJourney }>) => {
      if (event.data.type !== "result" || event.data.job !== input.job) return;
      worker.terminate();
      resolve(event.data.result!);
    };
    worker.onerror = (event) => {
      worker.terminate();
      reject(new Error(event.message || "RAPTOR worker failed"));
    };

    worker.postMessage(input, {
      transfer: [
        input.accessTimeMin.buffer,
        input.egressTimeMin.buffer,
        input.transferOffsets.buffer,
        input.transferTargets.buffer,
        input.transferTimes.buffer,
        input.routeOffsets.buffer,
        input.routeStopCounts.buffer,
        input.routeStops.buffer,
        input.routeDepartureOffsets.buffer,
        input.departures.buffer,
        input.routeSegmentOffsets.buffer,
        input.segmentTimes.buffer,
      ],
    });
  });
}
