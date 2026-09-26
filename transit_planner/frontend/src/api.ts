import type { NetworkPayload, ValidationResult } from "./types";

export async function validateNetwork(
  network: NetworkPayload,
): Promise<ValidationResult> {
  const response = await fetch("/api/v1/network/validate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(network),
  });

  if (!response.ok) {
    throw new Error("Сервер вернул ошибку проверки сети");
  }
  return response.json() as Promise<ValidationResult>;
}

async function loadJson<T>(
  path: string,
  params: URLSearchParams,
): Promise<T> {
  const response = await fetch(path + "?" + params.toString());
  if (!response.ok) {
    const body = await response.text();
    throw new Error(body || "Не удалось загрузить городские данные");
  }
  return response.json() as Promise<T>;
}

async function loadGeoJson(
  path: string,
  params: URLSearchParams,
): Promise<GeoJSON.FeatureCollection> {
  return loadJson<GeoJSON.FeatureCollection>(path, params);
}

export function loadOvertureStops(
  south: number,
  west: number,
  north: number,
  east: number,
): Promise<GeoJSON.FeatureCollection> {
  return loadGeoJson(
    "/api/v1/data/overture/stops",
    new URLSearchParams({
      south: String(south),
      west: String(west),
      north: String(north),
      east: String(east),
    }),
  );
}

export function loadOvertureRoads(
  south: number,
  west: number,
  north: number,
  east: number,
): Promise<GeoJSON.FeatureCollection> {
  return loadGeoJson(
    "/api/v1/data/overture/roads",
    new URLSearchParams({
      south: String(south),
      west: String(west),
      north: String(north),
      east: String(east),
    }),
  );
}

export function loadOvertureConnectors(
  south: number,
  west: number,
  north: number,
  east: number,
): Promise<GeoJSON.FeatureCollection> {
  return loadGeoJson(
    "/api/v1/data/overture/connectors",
    new URLSearchParams({
      south: String(south),
      west: String(west),
      north: String(north),
      east: String(east),
    }),
  );
}

export interface OvertureNetworkResponse {
  roads: GeoJSON.FeatureCollection;
  connectors: GeoJSON.FeatureCollection;
  stops: GeoJSON.FeatureCollection;
  places: GeoJSON.FeatureCollection;
  release: string;
  counts: {
    roads: number;
    connectors: number;
    stops: number;
    places: number;
  };
}

export function loadOvertureNetwork(
  south: number,
  west: number,
  north: number,
  east: number,
): Promise<OvertureNetworkResponse> {
  return loadJson<OvertureNetworkResponse>(
    "/api/v1/data/overture/network",
    new URLSearchParams({
      south: String(south),
      west: String(west),
      north: String(north),
      east: String(east),
    }),
  );
}

export interface OvertureRouteResponse {
  type: "Feature";
  geometry: {
    type: "LineString";
    coordinates: number[][];
  };
  properties: {
    edge_ids: string[];
    length_m: number;
    travel_time_min: number;
    snap_distances_m: number[];
  };
}

export function loadOvertureRoute(
  points: Array<{ lon: number; lat: number }>,
  south: number,
  west: number,
  north: number,
  east: number,
): Promise<OvertureRouteResponse> {
  return fetch("/api/v1/data/overture/route", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      points,
      south,
      west,
      north,
      east,
    }),
  }).then(async (response) => {
    if (!response.ok) {
      const body = await response.text();
      throw new Error(body || "Не удалось построить маршрут по Overture");
    }
    return response.json() as Promise<OvertureRouteResponse>;
  });
}

export interface TimetableResponse {
  service_id: string;
  periods: Array<{ period_id: string; departures_minute: number[] }>;
}

export function createTimetable(
  serviceId: string,
  periods: NetworkPayload["periods"],
  headwayByPeriod: Record<string, number>,
  offsetMinute = 0,
): Promise<TimetableResponse> {
  return fetch("/api/v1/timetable", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      service_id: serviceId,
      periods: Object.fromEntries(
        periods.map((period) => [period.id, { start_minute: period.start_minute, end_minute: period.end_minute }]),
      ),
      headway_by_period: headwayByPeriod,
      offset_minute: offsetMinute,
    }),
  }).then(async (response) => {
    if (!response.ok) throw new Error(await response.text() || "Не удалось создать расписание");
    return response.json() as Promise<TimetableResponse>;
  });
}
export interface AssignmentResponse {
  metrics: {
    total_trips: number;
    transit_trips: number;
    car_trips: number;
    walk_trips: number;
    bike_trips: number;
    transit_share: number;
    average_transit_time_min: number;
    average_transfers: number;
  };
  iterations: number;
  max_load_ratio: number;
  unserved_transit_demand: number;
  loss_reasons: Array<{ reason: string; trips: number }>;
  route_flows: Array<{ route_id: string; boardings: number; passenger_section_traversals: number }>;
  section_loads: Array<{ route_id: string; from_stop_id: string; to_stop_id: string; passengers: number; capacity: number; load_ratio: number }>;
  stop_flows: Array<{ stop_id: string; boardings: number; alightings: number; transfers: number }>;
}

export function calculateAssignment(
  network: NetworkPayload,
  demand: Array<{ origin_zone_id: string; destination_zone_id: string; trips_per_day: number; purpose?: string }>,
  zones: Array<{ id: string; centroid_x: number; centroid_y: number; population?: number; jobs?: number }>,
  periodId = "morning_peak",
): Promise<AssignmentResponse> {
  return fetch("/api/v1/assignment", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      network,
      demand,
      zones,
      config: { period_id: periodId },
    }),
  }).then(async (response) => {
    if (!response.ok) throw new Error(await response.text() || "Не удалось рассчитать пассажиропоток");
    return response.json() as Promise<AssignmentResponse>;
  });
}
export function loadDemandStreets(
  demand: Array<{ origin_zone_id: string; destination_zone_id: string; trips_per_day: number; purpose?: string }>,
  zones: Array<{ id: string; centroid_x: number; centroid_y: number }>,
  originLon: number,
  originLat: number,
): Promise<GeoJSON.FeatureCollection> {
  return fetch("/api/v1/demand/streets", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ demand, zones, origin_lon: originLon, origin_lat: originLat }),
  }).then(async (response) => {
    if (!response.ok) throw new Error(await response.text() || "Не удалось построить demand streets");
    return response.json() as Promise<GeoJSON.FeatureCollection>;
  });
}
export function loadPopulationZones(
  south: number,
  west: number,
  north: number,
  east: number,
): Promise<GeoJSON.FeatureCollection> {
  return loadGeoJson(
    "/api/v1/demand/population-zones",
    new URLSearchParams({
      south: String(south),
      west: String(west),
      north: String(north),
      east: String(east),
    }),
  );
}
export interface CityAssignmentResponse {
  data: { zones: number; places: number; od_pairs: number; total_demand_trips: number };
  assignment: AssignmentResponse;
}

export function calculateCityAssignment(
  network: NetworkPayload,
  south: number,
  west: number,
  north: number,
  east: number,
  originLon: number,
  originLat: number,
  periodId = "morning_peak",
): Promise<CityAssignmentResponse> {
  return fetch("/api/v1/assignment/city", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      network, south, west, north, east,
      origin_lon: originLon,
      origin_lat: originLat,
      config: { period_id: periodId },
    }),
  }).then(async (response) => {
    if (!response.ok) throw new Error(await response.text() || "Не удалось рассчитать городскую сеть");
    return response.json() as Promise<CityAssignmentResponse>;
  });
}