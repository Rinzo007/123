import type { NetworkPayload } from "../types";
import type { AssignmentResponse } from "../api";

export interface DemandZone { id: string; centroid_x: number; centroid_y: number; population?: number; jobs?: number; }
export interface DemandPair { origin_zone_id: string; destination_zone_id: string; trips_per_day: number; purpose?: string; base_time_min?: number; }

export function validateDemand(demand: DemandPair[], zones: DemandZone[]): void {
  const ids = new Set(zones.map(z => z.id));
  for (const d of demand) {
    if (!ids.has(d.origin_zone_id) || !ids.has(d.destination_zone_id)) throw new Error("OD содержит неизвестную зону");
    if (!Number.isFinite(d.trips_per_day) || d.trips_per_day < 0) throw new Error("OD имеет некорректный объём поездок");
  }
}

export function assignmentSummary(result: AssignmentResponse) {
  return {
    totalTrips: result.metrics.total_trips,
    transitTrips: result.metrics.transit_trips,
    transitShare: result.metrics.transit_share,
    maxLoadRatio: result.max_load_ratio,
    unservedTransitDemand: result.unserved_transit_demand,
    routeFlows: result.route_flows,
    sectionLoads: result.section_loads,
    stopFlows: result.stop_flows,
  };
}
