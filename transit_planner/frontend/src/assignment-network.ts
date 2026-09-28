/**
 * Network-derived inputs for assignment: section capacity, dwell, nearest
 * stop and headway feedback.
 *
 * Port of the network-derived helpers in `assignment.py`:
 * `_section_capacity_and_platforms`, `_stop_dwell_seconds`, `_nearest_stop_id`
 * and `_service_headway_feedback`. `route_segment_run_time_min` is replaced by
 * the routing worker's straight-line segment time: curvature and speed-limit
 * refinement of the reference implementation is not ported.
 *
 * `tests/test_assignment_network_parity.py` compares these against the Python
 * helpers on the same network encoded in the frontend payload shape.
 */
import { headwayUnevennessFactor, MODE_PROFILES, type SectionCapacity } from "./assignment-model";
import type { NetworkPayload, TransitMode } from "./types";

export interface StopPlatforms {
  capacity: SectionCapacity[];
  platformM: Map<string, number>;
}

/** Service shape as carried in `NetworkPayload` (snake_case, wire format). */
type ServiceRow = NetworkPayload["services"][number];

interface GroupedService {
  serviceId: string;
  routeId: string;
  fromId: string;
  toId: string;
  scheduledDepartures: number;
  groupLimit: number;
  vehicleCapacity: number;
}

/** Segment pairs of a route, including the closing pair for a loop. */
export function segmentPairs(route: NetworkPayload["routes"][number]): Array<[string, string]> {
  const pairs: Array<[string, string]> = [];
  for (let i = 0; i + 1 < route.stop_ids.length; i += 1) {
    pairs.push([route.stop_ids[i], route.stop_ids[i + 1]]);
  }
  if (route.closed && route.stop_ids.length > 1) {
    pairs.push([route.stop_ids[route.stop_ids.length - 1], route.stop_ids[0]]);
  }
  return pairs;
}

function trackSectionForSegment(
  route: NetworkPayload["routes"][number],
  index: number,
): string | null {
  if (!route.track_section_ids || route.track_section_ids.length === 0) return null;
  return route.track_section_ids[index] ?? null;
}

/** Number of scheduled departures of a service inside a period. */
export function serviceDepartures(
  network: NetworkPayload,
  service: ServiceRow,
  periodId: string,
): number {
  const period = network.periods.find((item) => item.id === periodId);
  if (!period) throw new Error(`Period not found in network: ${periodId}`);
  const headway = service.headway_by_period[periodId];
  if (!headway) throw new Error(`Service ${service.id} has no headway in period ${periodId}`);
  const offset = service.departure_offset_by_period?.[periodId] ?? 0;
  const first = period.start_minute
    + (((offset - period.start_minute) % headway) + headway) % headway;
  if (first >= period.end_minute) return 0;
  return Math.ceil((period.end_minute - first) / headway);
}

/**
 * Section capacities and platform lengths for a period.
 *
 * Where several services share one physical track corridor the corridor's
 * carrying capacity is allocated by scheduled share, so shared segments are
 * not double-counted while the full physical capacity is still available for
 * crowding analysis.
 */
export function sectionCapacityAndPlatforms(
  network: NetworkPayload,
  periodId: string,
): StopPlatforms {
  const period = network.periods.find((item) => item.id === periodId);
  if (!period) throw new Error(`Period not found in network: ${periodId}`);
  const durationHours = (period.end_minute - period.start_minute) / 60;

  const grouped = new Map<string, { kind: "route" | "track"; groupName: string; items: GroupedService[] }>();
  const physicalLimits = new Map<string, number>();
  const platformM = new Map<string, number>();
  const routeById = new Map(network.routes.map((route) => [route.id, route]));

  for (const service of network.services) {
    const headway = service.headway_by_period[periodId];
    if (headway === undefined) continue;
    const route = routeById.get(service.route_id);
    if (!route) throw new Error(`Service ${service.id} references unknown route ${service.route_id}`);
    const profile = MODE_PROFILES[route.mode];
    if (!profile) throw new Error(`Unknown mode: ${route.mode}`);
    const vehicle = network.vehicle_types.find((item) => item.id === service.vehicle_type_id);
    if (!vehicle) {
      throw new Error(`Service ${service.id} references unknown vehicle type ${service.vehicle_type_id}`);
    }
    const vehicleCapacity = vehicle.capacity || profile.capacity;
    const scheduledDepartures = (period.end_minute - period.start_minute) / headway;

    segmentPairs(route).forEach(([fromId, toId], index) => {
      const trackId = trackSectionForSegment(route, index);
      const track = trackId ? network.track_sections.find((item) => item.id === trackId) : undefined;
      let key: string;
      let groupName: string;
      let groupLimit: number;
      if (!track) {
        key = `route|${route.id}:${fromId}:${toId}`;
        groupName = "";
        groupLimit = profile.trackCapacityPerHour;
      } else {
        groupName = track.shared_group || track.id;
        key = `track|${groupName}`;
        groupLimit = Math.min(profile.trackCapacityPerHour, track.capacity_departures_per_hour);
        physicalLimits.set(
          groupName,
          Math.min(physicalLimits.get(groupName) ?? Infinity, track.capacity_departures_per_hour),
        );
      }
      // Platform length is a mode-level infrastructure requirement and does not
      // depend on an explicit track section being attached.
      for (const stopId of [fromId, toId]) {
        platformM.set(stopId, Math.max(platformM.get(stopId) ?? 0, profile.platformM));
      }
      const bucket = grouped.get(key) ?? { kind: key.startsWith("track|") ? "track" : "route", groupName, items: [] };
      bucket.items.push({
        serviceId: service.id,
        routeId: route.id,
        fromId,
        toId,
        scheduledDepartures,
        groupLimit,
        vehicleCapacity,
      });
      grouped.set(key, bucket);
    });
  }

  const capacities = new Map<string, number>();
  for (const bucket of grouped.values()) {
    const scheduledTotal = bucket.items.reduce((sum, item) => sum + item.scheduledDepartures, 0);
    const groupDepartures = bucket.kind === "track"
      ? (physicalLimits.get(bucket.groupName) ?? 0) * durationHours
      : null;
    if (bucket.kind === "track" && scheduledTotal <= 0) continue;
    for (const item of bucket.items) {
      const effective = groupDepartures === null
        ? item.scheduledDepartures
        : (groupDepartures * item.scheduledDepartures) / scheduledTotal;
      const capacity = Math.min(effective, item.groupLimit * durationHours) * item.vehicleCapacity;
      const forward = `${item.routeId}|${item.fromId}|${item.toId}`;
      capacities.set(forward, (capacities.get(forward) ?? 0) + capacity);
      if (routeById.get(item.routeId)?.both_ways) {
        const reverse = `${item.routeId}|${item.toId}|${item.fromId}`;
        capacities.set(reverse, (capacities.get(reverse) ?? 0) + capacity);
      }
    }
  }

  return {
    capacity: [...capacities.entries()].map(([key, capacity]) => {
      const [routeId, fromStopId, toStopId] = key.split("|");
      return { routeId, fromStopId, toStopId, capacity };
    }),
    platformM,
  };
}

/** Dwell seconds at a stop, linear in boardings so callers can hoist the part. */
export interface StopDwellCoefficients {
  base: Map<string, number>;
  perPassenger: Map<string, number>;
}

export function stopDwellCoefficients(
  network: NetworkPayload,
  periodId: string,
): StopDwellCoefficients {
  const base = new Map<string, number>();
  const perPassenger = new Map<string, number>();
  for (const service of network.services) {
    const headway = service.headway_by_period[periodId];
    if (headway === undefined) continue;
    const route = network.routes.find((item) => item.id === service.route_id);
    if (!route) continue;
    const profile = MODE_PROFILES[route.mode];
    if (!profile) throw new Error(`Unknown mode: ${route.mode}`);
    const departures = serviceDepartures(network, service, periodId);
    for (const stopId of route.stop_ids) {
      base.set(stopId, (base.get(stopId) ?? 0) + departures * profile.dwellS);
      perPassenger.set(stopId, (perPassenger.get(stopId) ?? 0) + profile.dwellPerPassengerS);
    }
  }
  return { base, perPassenger };
}

export function stopDwellFromCoefficients(
  coefficients: StopDwellCoefficients,
  stopId: string,
  boardings: number,
): number {
  return (coefficients.base.get(stopId) ?? 0) + (coefficients.perPassenger.get(stopId) ?? 0) * boardings;
}

/**
 * Nearest stop a zone can reach, limited by both the configured radius and the
 * mode's access distance.
 */
export function nearestStopId(
  network: NetworkPayload,
  zone: { centroidX: number; centroidY: number },
  maxDistanceM: number,
  periodId: string,
): string | null {
  let bestId: string | null = null;
  let bestDistance = maxDistanceM;

  const accessLimits = new Map<string, number>();
  for (const service of network.services) {
    if (service.headway_by_period[periodId] === undefined) continue;
    const route = network.routes.find((item) => item.id === service.route_id);
    if (!route) continue;
    const profile = MODE_PROFILES[route.mode];
    if (!profile) throw new Error(`Unknown mode: ${route.mode}`);
    for (const stopId of route.stop_ids) {
      accessLimits.set(stopId, Math.max(accessLimits.get(stopId) ?? 0, profile.accessM));
    }
  }

  for (const stop of network.stops) {
    const allowed = Math.min(maxDistanceM, accessLimits.get(stop.id) ?? 0);
    if (allowed <= 0) continue;
    const distance = Math.hypot(stop.location.x - zone.centroidX, stop.location.y - zone.centroidY);
    if (distance <= allowed && distance <= bestDistance) {
      bestDistance = distance;
      bestId = stop.id;
    }
  }
  return bestId;
}

/** Headway feedback multiplier per service for the observed boardings. */
export function serviceHeadwayFactors(
  network: NetworkPayload,
  periodId: string,
  serviceStopBoardings: ReadonlyMap<string, number>,
): Map<string, number> {
  const period = network.periods.find((item) => item.id === periodId);
  if (!period) throw new Error(`Period not found in network: ${periodId}`);
  const periodHours = (period.end_minute - period.start_minute) / 60;
  const factors = new Map<string, number>();
  for (const service of network.services) {
    const headway = service.headway_by_period[periodId];
    if (headway === undefined) continue;
    const route = network.routes.find((item) => item.id === service.route_id);
    if (!route) throw new Error(`Service ${service.id} references unknown route ${service.route_id}`);
    factors.set(service.id, headwayUnevennessFactor({
      mode: route.mode as TransitMode,
      headwayMin: headway,
      periodHours,
      stopBoardings: route.stop_ids.map((stopId) => serviceStopBoardings.get(`${service.id}|${stopId}`) ?? 0),
      routeClosed: route.closed ?? false,
      bothWays: route.both_ways ?? true,
    }));
  }
  return factors;
}

/** Straight-line segment run time in minutes, by mode speed. */
const MODE_SPEED_KPH: Record<TransitMode, number> = { bus: 18, tram: 19, metro: 70, rail: 58 };

export function segmentRunTimes(
  network: NetworkPayload,
): Map<string, number> {
  const stops = new Map(network.stops.map((stop) => [stop.id, stop]));
  const times = new Map<string, number>();
  for (const route of network.routes) {
    const speed = MODE_SPEED_KPH[route.mode];
    segmentPairs(route).forEach(([fromId, toId]) => {
      const from = stops.get(fromId);
      const to = stops.get(toId);
      if (!from || !to) return;
      const km = Math.hypot(to.location.x - from.location.x, to.location.y - from.location.y) / 1000;
      times.set(`${route.id}|${fromId}|${toId}`, (km / speed) * 60);
    });
  }
  return times;
}
