import type { NetworkPayload } from "../types";

export interface RouteBuildResult { network: NetworkPayload; routeId: string; }

export class RouteEditor {
  constructor(private readonly network: NetworkPayload) {}

  buildRoute(routeId: string, name: string, mode: NetworkPayload["routes"][number]["mode"], trackSectionIds: string[], stopIds: string[]): RouteBuildResult {
    if (!trackSectionIds.length) throw new Error("Маршрут должен содержать участки сети");
    const known = new Set(this.network.track_sections.map(s => s.id));
    const missing = trackSectionIds.filter(id => !known.has(id));
    if (missing.length) throw new Error("Неизвестные участки: " + missing.join(", "));
    const stops = new Set(this.network.stops.map(s => s.id));
    if (stopIds.some(id => !stops.has(id))) throw new Error("Маршрут содержит неизвестную остановку");
    const next = structuredClone(this.network);
    const route = { id: routeId, name, mode, stop_ids: [...stopIds], geometry: null, track_section_ids: [...trackSectionIds], both_ways: true, closed: false };
    const index = next.routes.findIndex(r => r.id === routeId);
    if (index >= 0) next.routes[index] = route; else next.routes.push(route);
    return { network: next, routeId };
  }

  removeRoute(routeId: string): NetworkPayload {
    const next = structuredClone(this.network);
    next.routes = next.routes.filter(r => r.id !== routeId);
    next.services = next.services.filter(s => s.route_id !== routeId);
    return next;
  }
}
