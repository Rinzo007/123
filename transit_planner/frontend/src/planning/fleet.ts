import type { NetworkPayload } from "../types";

export interface FleetRequirement { service_id: string; period_id: string; departures: number; cycle_minutes: number; headway_minutes: number; vehicles_required: number; }

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
