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