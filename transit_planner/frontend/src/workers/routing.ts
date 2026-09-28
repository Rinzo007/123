import { firstDepartureMinute } from "../planning/timetable";
import type { NetworkPayload, TransitMode } from "../types";

export interface RaptorJourney {
  found: boolean;
  arrivalMin: number;
  generalizedMin: number;
  transfers: number;
  routeIds: Int32Array;
  boardStops: Int32Array;
  alightStops: Int32Array;
  accessStop: number;
  egressStop: number;
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
  // Та же формула, что в timetable.py: остаток берётся неотрицательным.
  const first = firstDepartureMinute(start, headway, offset);
  if (first >= end) return [];
  const result: number[] = [];
  for (let value = first; value < end; value += headway) result.push(value);
  return result;
}

function pack(
  network: NetworkPayload,
  periodId: string,
  accessTimeMin: Float64Array,
  egressTimeMin: Float64Array,
  departureMin: number,
  rangeWindowMin: number,
  maxTransfers: number,
) {
  const stopCount = network.stops.length;
  const routePatterns: Array<{
    routeId: string;
    stops: number[];
    departures: number[];
    segmentTimes: number[];
  }> = [];
  const index = new Map(network.stops.map((stop, i) => [stop.id, i]));
  const period = network.periods.find((item) => item.id === periodId);
  if (!period) throw new Error(`Period not found in network: ${periodId}`);
  if (network.services.length === 0) throw new Error("Network has no services: build a route before planning");
  if (accessTimeMin.length !== stopCount || egressTimeMin.length !== stopCount) {
    throw new Error("Access/egress maps must cover every network stop");
  }
  if (!accessTimeMin.some((value) => Number.isFinite(value))) {
    throw new Error("No transit stop is reachable on foot from the origin");
  }
  if (!egressTimeMin.some((value) => Number.isFinite(value))) {
    throw new Error("No transit stop can reach the destination on foot");
  }

  let patternsWithoutHeadway = 0;
  for (const service of network.services) {
    const route = network.routes.find((item) => item.id === service.route_id);
    // Отсутствие маршрута или неизвестная остановка в маршруте — это порча
    // данных, молча пропускать их нельзя.
    if (!route) throw new Error(`Service ${service.id} references unknown route ${service.route_id}`);
    const headway = service.headway_by_period[periodId];
    // Маршрут может не ходить в этом периоде — это законно, но если не ходит
    // никто, «нет соединения» не должно выглядеть как «нет расписания».
    if (!headway) {
      patternsWithoutHeadway += 1;
      continue;
    }

    const unknownStop = route.stop_ids.find((id) => !index.has(id));
    if (unknownStop !== undefined) {
      throw new Error(`Route ${route.id} references stop missing from network: ${unknownStop}`);
    }
    const stops = route.stop_ids.map((id) => index.get(id)!);
    if (stops.length < 2) throw new Error(`Route ${route.id} has fewer than two stops`);

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
        period.start_minute,
        period.end_minute,
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
          period.start_minute,
          period.end_minute,
          headway,
          service.departure_offset_by_period?.[periodId] ?? 0,
        ),
        segmentTimes: reverseTimes,
      });
    }
  }

  // Порядок важен: на пустом массиве every() истинно вакуумно, поэтому
  // пустой набор паттернов проверяется первым и даёт точное сообщение.
  if (routePatterns.length === 0) {
    throw new Error(
      patternsWithoutHeadway === network.services.length
        ? `No service has a headway in period ${periodId}: set headways before planning`
        : `No route pattern is usable in period ${periodId}`,
    );
  }
  if (routePatterns.every((pattern) => pattern.departures.length === 0)) {
    throw new Error(`No departure falls inside period ${periodId}: headways are longer than the period`);
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

  // Access/egress maps arrive from the street-walk layer already sized to
  // stopCount; they are posted to the worker, whose transfer neuters them.

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
    input: {
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
      departureMin,
    },
    // Копия нужна для геометрии маршрута: routeStops уходит в воркер по
    // transfer и в главном потоке обнуляется. Воркеру она не нужна, поэтому
    // наружу не отправляется.
    patterns: routePatterns.map((pattern) => ({
      routeId: pattern.routeId,
      stops: pattern.stops.slice(),
    })),
  };
}
export interface RaptorRoutePattern {
  routeId: string;
  stops: number[];
}

export interface RaptorRouteResult {
  journey: RaptorJourney;
  patterns: RaptorRoutePattern[];
}

export function routeWithRaptor(
  network: NetworkPayload,
  periodId: string,
  accessTimeMin: Float64Array,
  egressTimeMin: Float64Array,
  departureMin = 420,
  rangeWindowMin = 30,
  maxTransfers = 4,
): Promise<RaptorRouteResult> {
  const { input, patterns } = pack(
    network,
    periodId,
    accessTimeMin,
    egressTimeMin,
    departureMin,
    rangeWindowMin,
    maxTransfers,
  );

  const worker = new Worker(new URL("./routing.worker.ts", import.meta.url), { type: "module" });
  return new Promise((resolve, reject) => {
    worker.onmessage = (event: MessageEvent<{ type?: string; job?: number; result?: RaptorJourney }>) => {
      if (event.data.type !== "result" || event.data.job !== input.job) return;
      worker.terminate();
      resolve({ journey: event.data.result!, patterns });
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
