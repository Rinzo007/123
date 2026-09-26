export type TransitMode = "bus" | "tram" | "metro" | "rail";

export interface StopDraft {
  id: string;
  name: string;
  lon: number;
  lat: number;
}

export interface NetworkPayload {
  origin_lon?: number;
  origin_lat?: number;
  stops: Array<{
    id: string;
    name: string;
    location: { x: number; y: number };
    is_station: boolean;
  }>;
  routes: Array<{
    id: string;
    name: string;
    mode: TransitMode;
    stop_ids: string[];
    geometry: { points: Array<{ x: number; y: number }> } | null;
  }>;
  vehicle_types: Array<{
    id: string;
    name: string;
    mode: TransitMode;
    capacity: number;
    operating_cost_per_km: number;
  }>;
  periods: Array<{
    id: string;
    start_minute: number;
    end_minute: number;
  }>;
  services: Array<{
    id: string;
    route_id: string;
    vehicle_type_id: string;
    headway_by_period: Record<string, number>;
  }>;
}

export interface ValidationResult {
  valid: boolean;
  errors: string[];
}
