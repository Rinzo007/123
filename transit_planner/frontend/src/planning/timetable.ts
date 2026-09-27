import type { NetworkPayload } from "../types";

export interface DeparturePlan { period_id: string; departures_minute: number[]; }

export function generateDepartures(period: NetworkPayload["periods"][number], headway: number, offset = 0): DeparturePlan {
  if (!Number.isFinite(headway) || headway <= 0) throw new Error("Интервал должен быть больше нуля");
  const first = period.start_minute + Math.max(0, offset);
  const departures: number[] = [];
  for (let t = first; t < period.end_minute; t += headway) departures.push(t);
  return { period_id: period.id, departures_minute: departures };
}

export function generateServiceTimetable(network: NetworkPayload, serviceId: string): DeparturePlan[] {
  const service = network.services.find(s => s.id === serviceId);
  if (!service) throw new Error("Service not found: " + serviceId);
  return network.periods.map(p => {
    const headway = service.headway_by_period[p.id];
    if (headway == null || headway <= 0) return { period_id: p.id, departures_minute: [] };
    return generateDepartures(p, headway, service.departure_offset_by_period?.[p.id] ?? 0);
  });
}
