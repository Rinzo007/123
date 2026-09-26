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
  track_capacity: Array<{
    shared_group: string;
    period_id: string;
    route_ids: string[];
    tph: number;
    limit_tph: number;
    utilization: number;
  }>;
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
  section_loads: Array<{ route_id: string; from_stop_id: string; to_stop_id: string; passengers: number; capacity: number; load_ratio: number; crowding_level: string }>;
  stop_flows: Array<{ stop_id: string; boardings: number; alightings: number; transfers: number; dwell_seconds: number; platform_m: number }>;
}

export function calculateAssignment(
  network: NetworkPayload,
  demand: Array<{ origin_zone_id: string; destination_zone_id: string; trips_per_day: number; purpose?: string }>,
  zones: Array<{ id: string; centroid_x: number; centroid_y: number; population?: number; jobs?: number }>,
  periodId = "am",
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
  economics: EconomicsResult;
  periods: Array<{
    period_id: string;
    demand_trips: number;
    transit_trips: number;
    car_trips: number;
    walk_trips: number;
    bike_trips: number;
    transit_share: number;
    average_transit_time_min: number;
    average_transfers: number;
    max_load_ratio: number;
    services: Array<{
      service_id: string;
      route_id: string;
      period_id: string;
      departures: number;
      fleet: number;
      riders: number;
      peak_load_factor: number;
      effective_headway_min: number;
      minimum_station_headway_min: number;
      minimum_headway_min: number;
      minimum_headway_why: string;
      daily_vehicle_km: number;
      daily_opex: number;
    }>;
    economics: EconomicsResult;
  }>;
}

export function calculateCityAssignment(
  network: NetworkPayload,
  south: number,
  west: number,
  north: number,
  east: number,
  originLon: number,
  originLat: number,
  periodId = "am",
  farePerTransitTrip = 0,
  annualDays = 365,
): Promise<CityAssignmentResponse> {
  return fetch("/api/v1/assignment/city", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      network, south, west, north, east,
      origin_lon: originLon,
      origin_lat: originLat,
      config: { period_id: periodId },
      economics_config: {
        period_id: periodId,
        fare_per_transit_trip: Math.max(0, farePerTransitTrip),
        annual_days: Math.max(1, Math.min(366, Math.round(annualDays))),
      },
    }),
  }).then(async (response) => {
    if (!response.ok) throw new Error(await response.text() || "Не удалось рассчитать городскую сеть");
    return response.json() as Promise<CityAssignmentResponse>;
  });
}

export interface ScenarioComparisonResponse {
  base: {
    scenario_id: string;
    name: string;
    metrics: Record<string, number>;
  };
  alternative: {
    scenario_id: string;
    name: string;
    metrics: Record<string, number>;
  };
  comparison: {
    base_scenario_id: string;
    alternative_scenario_id: string;
    metrics: Array<{
      metric: string;
      base: number;
      alternative: number;
      delta: number;
      relative_delta: number;
    }>;
    sections: Array<{
      route_id: string;
      from_stop_id: string;
      to_stop_id: string;
      base_passengers: number;
      alternative_passengers: number;
      delta: number;
    }>;
    services: Array<{
      service_id: string;
      route_id: string;
      period_id: string;
      base_riders: number;
      alternative_riders: number;
      riders_delta: number;
      base_peak_load_factor: number;
      alternative_peak_load_factor: number;
      peak_load_factor_delta: number;
      base_fleet: number;
      alternative_fleet: number;
      fleet_delta: number;
      base_effective_headway_min: number;
      alternative_effective_headway_min: number;
      effective_headway_delta: number;
      base_minimum_headway_min: number;
      alternative_minimum_headway_min: number;
      minimum_headway_delta: number;
    }>;
  };
}

export interface ScenarioPayload {
  id: string;
  name: string;
  network: NetworkPayload;
  demand: Array<{
    origin_zone_id: string;
    destination_zone_id: string;
    trips_per_day: number;
    purpose?: string;
    base_time_min?: number;
  }>;
  zones: Array<{
    id: string;
    centroid_x: number;
    centroid_y: number;
    population?: number;
    jobs?: number;
    no_car_share?: number;
  }>;
  config: { period_id: string };
  economics_config?: {
    period_id?: string;
    fare_per_transit_trip?: number;
    annual_days?: number;
  };
}

export interface EconomicsResult {
  daily_vehicle_km: number;
  daily_fleet_cost: number;
  daily_operating_cost: number;
  daily_fare_revenue: number;
  annual_fleet_cost: number;
  annual_operating_cost: number;
  annual_fare_revenue: number;
  capital_cost: number;
  operating_cost_per_transit_trip: number;
  revenue_per_transit_trip: number;
}

export interface EconomicsResponse {
  scenario_id: string;
  name: string;
  economics: EconomicsResult;
}

export function calculateEconomics(
  network: NetworkPayload,
  demand: ScenarioPayload["demand"],
  zones: ScenarioPayload["zones"],
  periodId = "am",
  farePerTransitTrip = 0,
  annualDays = 365,
): Promise<EconomicsResponse> {
  return fetch("/api/v1/economics", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      id: "economics",
      name: "Текущий сценарий",
      network,
      demand,
      zones,
      config: { period_id: periodId },
      economics_config: {
        period_id: periodId,
        fare_per_transit_trip: farePerTransitTrip,
        annual_days: annualDays,
      },
    }),
  }).then(async (response) => {
    if (!response.ok) {
      throw new Error(await response.text() || "Не удалось рассчитать экономику");
    }
    return response.json() as Promise<EconomicsResponse>;
  });
}

export function compareScenarios(
  base: ScenarioPayload,
  alternative: ScenarioPayload,
): Promise<ScenarioComparisonResponse> {
  return fetch("/api/v1/scenario/compare", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ base, alternative }),
  }).then(async (response) => {
    if (!response.ok) {
      throw new Error(await response.text() || "Не удалось сравнить сценарии");
    }
    return response.json() as Promise<ScenarioComparisonResponse>;
  });
}
