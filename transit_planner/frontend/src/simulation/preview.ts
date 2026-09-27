import type { NetworkPayload } from "../types";

export type PlanningPreview = {
  lines: number;
  stops: number;
  dailyDepartures: number;
  fleetEstimate: number;
  routeLengthKm: number;
  capitalCost: number;
  signature: string;
};

export type ScenarioProbe = {
  base: PlanningPreview;
  candidate: PlanningPreview;
  delta: {
    lines: number;
    stops: number;
    dailyDepartures: number;
    fleetEstimate: number;
    routeLengthKm: number;
    capitalCost: number;
  };
};

const DEFAULT_ROW_COST: Record<string, number> = {
  bus: 0.4,
  tram: 9,
  metro: 32,
  rail: 22,
};

function hashText(value: string): string {
  let hash = 2166136261;
  for (let i = 0; i < value.length; i += 1) {
    hash ^= value.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16);
}

export function networkSignature(network: NetworkPayload): string {
  const payload = {
    stops: network.stops,
    routes: network.routes,
    vehicle_types: network.vehicle_types,
    periods: network.periods,
    services: network.services,
  };
  return hashText(JSON.stringify(payload));
}

function pointDistanceKm(
  left: { x: number; y: number },
  right: { x: number; y: number },
): number {
  return Math.hypot(right.x - left.x, right.y - left.y) / 1000;
}

function routeLengthKm(network: NetworkPayload, route: NetworkPayload["routes"][number]): number {
  const points = route.geometry?.points;
  if (points && points.length >= 2) {
    let total = 0;
    for (let i = 1; i < points.length; i += 1) total += pointDistanceKm(points[i - 1], points[i]);
    return total;
  }
  let total = 0;
  const stops = new Map(network.stops.map((stop) => [stop.id, stop]));
  for (let i = 1; i < route.stop_ids.length; i += 1) {
    const a = stops.get(route.stop_ids[i - 1]);
    const b = stops.get(route.stop_ids[i]);
    if (a && b) total += pointDistanceKm(a.location, b.location);
  }
  return total;
}

export function planningPreview(network: NetworkPayload): PlanningPreview {
  let dailyDepartures = 0;
  let fleetEstimate = 0;
  let routeLengthKm = 0;
  let capitalCost = 0;

  for (const service of network.services) {
    const route = network.routes.find((item) => item.id === service.route_id);
    if (!route) continue;
    const length = routeLengthKm(network, route);
    routeLengthKm += length;

    const activeHeadways: number[] = [];
    for (const [periodId, headway] of Object.entries(service.headway_by_period)) {
      const period = network.periods.find((item) => item.id === periodId);
      if (!period || headway <= 0) continue;
      activeHeadways.push(headway);
      dailyDepartures += Math.ceil((period.end_minute - period.start_minute) / headway);
    }

    if (activeHeadways.length) {
      const cycleMinutes = route.stop_ids.length * 2 + 4 + length * 3;
      fleetEstimate += Math.max(1, Math.ceil(cycleMinutes / Math.min(...activeHeadways)));
    }

    const row = route.mode === "bus" || route.mode === "tram"
      ? (route.geometry ? "mixed" : "mixed")
      : route.mode === "metro" || route.mode === "rail"
        ? "reserved"
        : "mixed";
    capitalCost += length * (DEFAULT_ROW_COST[route.mode] ?? 1);
    if (row === "reserved" && route.mode === "metro") capitalCost += length * 0;
  }

  return {
    lines: network.routes.length,
    stops: network.stops.length,
    dailyDepartures,
    fleetEstimate,
    routeLengthKm,
    capitalCost,
    signature: networkSignature(network),
  };
}

export function keepNetwork(
  previous: NetworkPayload | null,
  current: NetworkPayload,
): NetworkPayload {
  if (!previous) return current;
  return networkSignature(previous) === networkSignature(current) ? previous : current;
}

export function segmentSignatures(network: NetworkPayload): Map<string, string> {
  const result = new Map<string, string>();
  const stops = new Map(network.stops.map((stop) => [stop.id, stop]));
  for (const route of network.routes) {
    for (let index = 0; index < route.stop_ids.length - 1; index += 1) {
      const from = stops.get(route.stop_ids[index]);
      const to = stops.get(route.stop_ids[index + 1]);
      const signature = JSON.stringify({
        route: route.id,
        segment: index,
        mode: route.mode,
        from: from?.location,
        to: to?.location,
        geometry: route.geometry,
      });
      result.set(\`\${route.id}:\${index}\`, hashText(signature));
    }
  }
  return result;
}

export function changedSegments(
  previous: Map<string, string> | null,
  network: NetworkPayload,
): string[] {
  if (!previous) return [...segmentSignatures(network).keys()];
  const next = segmentSignatures(network);
  const changed: string[] = [];
  for (const [key, signature] of next) {
    if (previous.get(key) !== signature) changed.push(key);
  }
  for (const key of previous.keys()) if (!next.has(key)) changed.push(key);
  return changed;
}

export function probe(
  base: NetworkPayload,
  candidate: NetworkPayload,
): ScenarioProbe {
  const left = planningPreview(base);
  const right = planningPreview(candidate);
  return {
    base: left,
    candidate: right,
    delta: {
      lines: right.lines - left.lines,
      stops: right.stops - left.stops,
      dailyDepartures: right.dailyDepartures - left.dailyDepartures,
      fleetEstimate: right.fleetEstimate - left.fleetEstimate,
      routeLengthKm: right.routeLengthKm - left.routeLengthKm,
      capitalCost: right.capitalCost - left.capitalCost,
    },
  };
}
