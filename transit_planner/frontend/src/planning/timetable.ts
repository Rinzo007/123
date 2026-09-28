import type { NetworkPayload } from "../types";

export interface DeparturePlan { period_id: string; departures_minute: number[]; }

/**
 * Остаток как в Python: для положительного делителя результат неотрицателен.
 * JS `%` для отрицательного операнда даёт отрицательный остаток, из-за чего
 * первое отправление уезжало до начала периода и расходилось с
 * timetable.py/network.py/routing.py.
 */
export function firstDepartureMinute(start: number, headway: number, offset = 0): number {
  const shift = ((offset - start) % headway + headway) % headway;
  return start + shift;
}

export function generateDepartures(period: NetworkPayload["periods"][number], headway: number, offset = 0): DeparturePlan {
  if (!Number.isFinite(headway) || headway <= 0) throw new Error("Интервал должен быть больше нуля");
  const first = firstDepartureMinute(period.start_minute, headway, offset);
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
