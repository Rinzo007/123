import type { NetworkPayload } from "../types";

export interface FleetRequirement { service_id: string; period_id: string; departures: number; cycle_minutes: number; headway_minutes: number; vehicles_required: number; }

export interface FleetPeriodInput {
  id: string;
  start_minute: number;
  end_minute: number;
}

function periodFleetValue(
  periods: FleetPeriodInput[],
  headwayByPeriod: Record<string, number>,
  cycleMinutes: number,
  minute: number,
): number {
  for (const period of periods) {
    if (period.start_minute <= minute && minute < period.end_minute) {
      const headway = headwayByPeriod[period.id] ?? 0;
      if (!(headway > 0)) return 0;
      return Math.ceil(cycleMinutes / headway);
    }
  }
  return 0;
}

/**
 * Vehicles needed at an exact minute, blending across period boundaries.
 *
 * Mirrors analytics.fleet_required_at_minute: within ±cycleMinutes of a
 * boundary the requirement ramps linearly between the adjacent periods'
 * ceil(cycle/headway) values; outside every window it is the containing
 * period's integer requirement, outside all periods 0. Nearest boundary wins.
 */
export function fleetRequiredAt(
  periods: FleetPeriodInput[],
  headwayByPeriod: Record<string, number>,
  cycleMinutes: number,
  minute: number,
): number {
  if (!(cycleMinutes > 0)) throw new Error("Оборотный цикл должен быть больше нуля");
  if (!Number.isFinite(minute)) throw new Error("Минута должна быть конечной");
  const ordered = [...periods].sort((a, b) => a.start_minute - b.start_minute);
  const base = periodFleetValue(ordered, headwayByPeriod, cycleMinutes, minute);
  const boundaries = [...new Set(ordered.flatMap((p) => [p.start_minute, p.end_minute]))];
  let nearest: number | null = null;
  for (const boundary of boundaries) {
    const distance = Math.abs(minute - boundary);
    if (distance < cycleMinutes && (nearest === null || distance < Math.abs(minute - nearest))) {
      nearest = boundary;
    }
  }
  if (nearest === null) return base;
  const epsilon = 1e-9;
  const before = periodFleetValue(ordered, headwayByPeriod, cycleMinutes, nearest - epsilon);
  const after = periodFleetValue(ordered, headwayByPeriod, cycleMinutes, nearest + epsilon);
  const raw = (minute - (nearest - cycleMinutes)) / (2 * cycleMinutes);
  const progress = Math.min(1, Math.max(0, raw));
  return before * (1 - progress) + after * progress;
}

export function estimateFleetRequirement(network: NetworkPayload, serviceId: string, cycleMinutes: number): FleetRequirement[] {
  if (cycleMinutes <= 0) throw new Error("Оборотный цикл должен быть больше нуля");
  const service = network.services.find(s => s.id === serviceId);
  if (!service) throw new Error("Service not found: " + serviceId);
  return network.periods.map(period => {
    const headway = service.headway_by_period[period.id];
    if (headway == null || headway <= 0) {
      return { service_id: serviceId, period_id: period.id, departures: 0, cycle_minutes: cycleMinutes, headway_minutes: 0, vehicles_required: 0 };
    }
    const departures = Math.max(0, Math.ceil((period.end_minute - period.start_minute) / headway));
    return { service_id: serviceId, period_id: period.id, departures, cycle_minutes: cycleMinutes, headway_minutes: headway, vehicles_required: Math.max(1, Math.ceil(cycleMinutes / headway)) };
  });
}
