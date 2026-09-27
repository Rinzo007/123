import type { NetworkPayload, TransitMode } from "../types";

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

const MODE_SPEED_KPH: Record<TransitMode, number> = {
  bus: 18,
  tram: 19,
  metro: 70,
  rail: 58,
};

let requestId = 0;

function segmentTime(
  network: NetworkPayload,
  route: NetworkPayload["routes"][number],
  index: number,
): number {
  const stops = new Map(network.stops.map((stop) => [stop.id, stop]));
  const from = stops.get(route.stop_ids[index]);
  const to = stops.get(route.stop_ids[index + 1]);
  if (!from || !to) return 0;
  const distanceKm = Math.hypot(
    to.location.x - from.location.x,
    to.location.y - from.location.y,
  ) / 1000;
  return distanceKm / MODE_SPEED_KPH[route.mode] * 60;
}

function departures(start: number, end: number, headway: number, offset: number): number[] {
  if (headway <= 0) return [];
  const first = start + ((offset - start) % headway);
  if (first >= end) return [];
  const result: number[] = [];
  for (let value = first; value < end; value += headway) result.push(value);
  return result;
}

function pack(network: NetworkPayload, periodId: string, origin: number, destination: number, departureMin: number, rangeWindowMin: number, maxTransfers: number) {
  const stopCount = network.stops.length;
  const routePatterns: Array<{
    routeId: string;
    stops: number[];
    departures: number[];
    segmentTimes: number[];
  }> = [];
  const index = new Map(network.stops.map((stop, i) => [stop.id, i]));

  for (const service of network.services) {
    const route = network.routes.find((item) => item.id === service.route_id);
    const headway = service.headway_by_period[periodId];
    if (!route || !headway) continue;

    const stops = route.stop_ids
      .map((id) => index.get(id))
      .filter((value): value is number => value !== undefined);
    if (stops.length < 2) continue;

    const segmentTimes: number[] = [];
    for (let i = 0; i < route.stop_ids.length - 1; i += 1) {
      segmentTimes.push(segmentTime(network, route, i));
    }
    const patternStops = route.closed ? [...stops, stops[0]] : stops;
    const patternTimes = segmentTimes;
    routePatterns.push({
      routeId: route.id,
      stops: patternStops,
      departures: departures(
        network.periods.find((item) => item.id === periodId)?.start_minute ?? 0,
        network.periods.find((item) => item.id === periodId)?.end_minute ?? 1440,
        headway,
        service.departure_offset_by_period?.[periodId] ?? 0,
      ),
      segmentTimes: patternTimes,
    });

    if (route.both_ways) {
      const reverseStops = route.closed ? [...stops].reverse().concat(stops[stops.length - 1]) : [...stops].reverse();
      const reverseTimes = route.closed
        ? [...segmentTimes.slice(0, -1)].reverse().concat(segmentTimes[segmentTimes.length - 1])
        : [...segmentTimes].reverse();
      routePatterns.push({
        routeId: route.id,
        stops: reverseStops,
        departures: departures(
          network.periods.find((item) => item.id === periodId)?.start_minute ?? 0,
          network.periods.find((item) => item.id === periodId)?.end_minute ?? 1440,
          headway,
          service.departure_offset_by_period?.[periodId] ?? 0,
        ),
        segmentTimes: reverseTimes,
      });
    }
  }

  const routeOffsets = new Int32Array(routePatterns.length + 1);
  const routeStopCounts = new Int32Array(routePatterns.length);
  const routeDepartureOffsets = new Int32Array(routePatterns.length + 1);
  const routeSegmentOffsets = new Int32Array(routePatterns.length + 1);
  let stopTotal = 0;
  let departureTotal = 0;
  let segmentTotal = 0;
  for (let i = 0; i < routePatterns.length; i += 1) {
    routeOffsets[i] = stopTotal;
    routeStopCounts[i] = routePatterns[i].stops.length;
    stopTotal += routePatterns[i].stops.length;
    routeDepartureOffsets[i] = departureTotal;
    departureTotal += routePatterns[i].departures.length;
    routeSegmentOffsets[i] = segmentTotal;
    segmentTotal += routePatterns[i].segmentTimes.length;
  }
  routeOffsets[routePatterns.length] = stopTotal;
  routeDepartureOffsets[routePatterns.length] = departureTotal;
  routeSegmentOffsets[routePatterns.length] = segmentTotal;

  const routeStops = new Int32Array(stopTotal);
  const routeDepartures = new Float64Array(departureTotal);
  const routeSegmentTimes = new Float64Array(segmentTotal);
  let si = 0;
  let di = 0;
  let ti = 0;
  for (const pattern of routePatterns) {
    routeStops.set(pattern.stops, si);
    routeDepartures.set(pattern.departures, di);
    routeSegmentTimes.set(pattern.segmentTimes, ti);
    si += pattern.stops.length;
    di += pattern.departures.length;
    ti += pattern.segmentTimes.length;
  }

  const accessTimeMin = new Float64Array(stopCount);
  const egressTimeMin = new Float64Array(stopCount);
  accessTimeMin.fill(Number.POSITIVE_INFINITY);
  egressTimeMin.fill(Number.POSITIVE_INFINITY);
  accessTimeMin[origin] = 0;
  egressTimeMin[destination] = 0;

  const transferPairs: Array<[number, number, number]> = [];
  for (let a = 0; a < stopCount; a += 1) {
    for (let b = a + 1; b < stopCount; b += 1) {
      const minutes = Math.hypot(
        network.stops[b].location.x - network.stops[a].location.x,
        network.stops[b].location.y - network.stops[a].location.y,
      ) / 1000 / 5 * 60;
      if (minutes <= 6) {
        transferPairs.push([a, b, minutes], [b, a, minutes]);
      }
    }
  }

  const transferOffsets = new Int32Array(stopCount + 1);
  for (const [from] of transferPairs) transferOffsets[from + 1] += 1;
  for (let i = 0; i < stopCount; i += 1) transferOffsets[i + 1] += transferOffsets[i];
  const cursor = transferOffsets.slice(0, stopCount);
  const transferTargets = new Int32Array(transferPairs.length);
  const transferTimes = new Float64Array(transferPairs.length);
  for (const [from, to, minutes] of transferPairs) {
    const at = cursor[from]++;
    transferTargets[at] = to;
    transferTimes[at] = minutes;
  }

  return {
    type: "route",
    job: ++requestId,
    stopCount,
    routeCount: routePatterns.length,
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
    departures: routeDepartures,
    routeSegmentOffsets,
    segmentTimes: routeSegmentTimes,
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
  const input = pack(
    network,
    periodId,
    origin,
    destination,
    departureMin,
    rangeWindowMin,
    maxTransfers,
  );

  const worker = new Worker(new URL("./routing.worker.ts", import.meta.url), { type: "module" });
  return new Promise((resolve, reject) => {
    worker.onmessage = (event: MessageEvent<{ type?: string; job?: number; result?: RaptorJourney }>) => {
      if (event.data.type !== "result" || event.data.job !== input.job) return;
      worker.terminate();
      resolve(event.data.result!);
    };
    worker.onerror = (event) => {
      worker.terminate();
      reject(new Error(event.message || "rRAPTOR worker failed"));
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
