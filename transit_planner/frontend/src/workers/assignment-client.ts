/**
 * Main-thread wrapper around the assignment worker.
 *
 * The heavy loop lives in `assignment-runtime.ts`; this module only owns the
 * worker lifecycle and maps the result onto the snake_case shape the UI
 * already consumes, so the interface does not ripple through the app.
 */
import type { AssignmentResponse } from "../api";
import type { FeatureCollection, Position } from "../geojson";
import type { AssignmentPair, AssignmentZone } from "../assignment-model";
import type { NetworkPayload } from "../types";
import type {
  AssignmentWorkerInput,
  AssignmentWorkerResult,
} from "./assignment-runtime";

let requestId = 0;

export interface AssignmentWorkerOptions {
  departureMin: number;
  rangeWindowMin?: number;
  maxTransfers?: number;
}

export function assignDemandInWorker(
  network: NetworkPayload,
  periodId: string,
  pairs: readonly AssignmentPair[],
  zones: readonly AssignmentZone[],
  options: AssignmentWorkerOptions,
): Promise<AssignmentResponse> {
  if (pairs.length === 0) {
    throw new Error("Нет OD-пар: сначала постройте зоны населения и спрос");
  }
  if (zones.length === 0) {
    throw new Error("Нет зон населения: обновите «Данные Overture» (WorldPop)");
  }
  const worker = new Worker(new URL("./assignment.worker.ts", import.meta.url), { type: "module" });
  const job = ++requestId;
  return new Promise((resolve, reject) => {
    worker.onmessage = (event: MessageEvent<AssignmentWorkerResult | { type: "error"; job: number; message: string }>) => {
      const data = event.data;
      if (data.type === "error" && data.job === job) {
        worker.terminate();
        reject(new Error(data.message));
        return;
      }
      if (data.type !== "assigned" || data.job !== job) return;
      worker.terminate();
      resolve(toAssignmentResponse(data));
    };
    worker.onerror = (event) => {
      worker.terminate();
      reject(new Error(event.message || "assignment worker failed"));
    };
    const input: AssignmentWorkerInput = {
      job,
      network,
      periodId,
      pairs: pairs.map((pair) => ({ ...pair })),
      zones: zones.map((zone) => ({ ...zone })),
      departureMin: options.departureMin,
      rangeWindowMin: options.rangeWindowMin ?? 0,
      maxTransfers: options.maxTransfers ?? 4,
    };
    worker.postMessage(input);
  });
}

/**
 * Demand-street overlay built from the section loads.
 *
 * The server endpoint is not called: the flows it returned are already known
 * locally. Sections are drawn along the route they belong to, so the layer
 * shows where passengers actually are. `flow_weight` is what the map style
 * interpolates on.
 */
export function demandStreetsFromLoads(
  network: NetworkPayload,
  sectionLoads: AssignmentResponse["section_loads"],
): FeatureCollection {
  const stops = new Map(network.stops.map((stop) => [stop.id, stop]));
  const byRoute = new Map<string, AssignmentResponse["section_loads"]>();
  for (const section of sectionLoads) {
    if (section.passengers <= 0) continue;
    byRoute.set(section.route_id, [...(byRoute.get(section.route_id) ?? []), section]);
  }

  const features: FeatureCollection["features"] = [];
  for (const [routeId, sections] of byRoute) {
    const route = network.routes.find((item) => item.id === routeId);
    if (!route?.geometry) continue;
    // Порядок секций по маршруту: без него линия вышла бы зигзагом.
    const ordered = [...sections].sort(
      (a, b) => route.stop_ids.indexOf(a.from_stop_id) - route.stop_ids.indexOf(b.from_stop_id),
    );
    const points = ordered.flatMap((section) => {
      const from = stops.get(section.from_stop_id);
      const to = stops.get(section.to_stop_id);
      if (!from || !to) return [];
      return [
        [from.location.x, from.location.y] as Position,
        [to.location.x, to.location.y] as Position,
      ];
    });
    // Соседние секции делят остановку, и без свёртки стык даёт подряд идущие
    // дубли вершин — линия рисуется с узлом без причины.
    const deduped = points.filter(
      (point, index) => index === 0
        || point[0] !== points[index - 1][0]
        || point[1] !== points[index - 1][1],
    );
    if (deduped.length < 2) continue;
    features.push({
      type: "Feature",
      geometry: { type: "LineString", coordinates: deduped },
      properties: {
        route_id: routeId,
        flow_weight: Math.max(...ordered.map((section) => section.passengers)),
        passengers: ordered.reduce((sum, section) => sum + section.passengers, 0),
        sections: ordered.length,
      },
    });
  }
  return { type: "FeatureCollection", features };
}

/** Maps the worker result onto the API shape the UI already renders. */
function toAssignmentResponse(result: AssignmentWorkerResult): AssignmentResponse {
  return {
    // Ёмкости шин считаются в браузере, но таблица трекова у UI остаётся от
    // серверного контракта: без неё раздел не рисуется, а пустой массив там же
    // означает «не считалось» — поэтому поле намеренно пустое и не вводит в
    // заблуждение числом.
    track_capacity: [],
    metrics: {
      total_trips: result.metrics.totalTrips,
      transit_trips: result.metrics.transitTrips,
      car_trips: result.metrics.carTrips,
      walk_trips: result.metrics.walkTrips,
      bike_trips: result.metrics.bikeTrips,
      transit_share: result.metrics.transitShare,
      average_transit_time_min: result.metrics.averageTransitTimeMin,
      average_transfers: result.metrics.averageTransfers,
    },
    iterations: result.iterations,
    max_load_ratio: result.maxLoadRatio,
    unserved_transit_demand: result.unserved,
    loss_reasons: result.lossReasons,
    route_flows: result.routeFlows.map((route) => ({
      route_id: route.routeId,
      boardings: route.boardings,
      passenger_section_traversals: route.passengerSectionTraversals,
    })),
    section_loads: result.sectionLoads.map((section) => ({
      route_id: section.routeId,
      from_stop_id: section.fromStopId,
      to_stop_id: section.toStopId,
      passengers: section.passengers,
      capacity: section.capacity,
      load_ratio: section.loadRatio,
      crowding_level: section.crowdingLevel,
    })),
    stop_flows: result.stopFlows.map((stop) => ({
      stop_id: stop.stopId,
      boardings: stop.boardings,
      alightings: stop.alightings,
      transfers: stop.transfers,
      dwell_seconds: stop.dwellSeconds,
      platform_m: stop.platformM,
    })),
  };
}
