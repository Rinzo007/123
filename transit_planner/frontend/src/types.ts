export type TransitMode = "bus" | "tram" | "metro" | "rail";

export type TrackType = "surface" | "elevated" | "tunnel" | "trenched" | "ramp";

export type PlatformLayout = "side" | "island" | "center" | "express_local";
export type FareSystem = "flat" | "route" | "distance" | "zone";

export interface StopDraft {
  id: string;
  name: string;
  lon: number;
  lat: number;
}

export interface StationPayload { id: string; name: string; stop_id: string; platform_ids: string[]; group_id?: string | null; interchange: boolean; platform_length_m?: number | null; }
export interface PlatformPayload { id: string; station_id: string; length_m: number; track_ids: string[]; layout: PlatformLayout; number: number; }
export interface RollingStockPayload { id: string; name: string; vehicle_type_id: string; car_capacity: number; car_length_m: number; train_width_m: number; max_cars: number; max_speed_kph: number; acceleration_mps2: number; deceleration_mps2: number; lateral_acceleration_mps2: number; minimum_curve_radius_m: number; dwell_seconds: number; car_cost: number; train_operating_cost_per_hour: number; car_operating_cost_per_hour: number; track_maintenance_cost_per_km_year: number; station_maintenance_cost_per_year: number; tph_limit: number; }
export interface FareGroupPayload { id: string; name: string; fare_system: FareSystem; flat_fare: number; route_fares: Record<string, number>; transfer_policy: "none" | "free" | "time_window"; transfer_window_min: number; boarding_charge: number; per_km_rate: number; fare_cap?: number | null; zones: Array<{ id: string; name: string; zone_number: number }>; zone_base_fare: number; zone_per_zone_fare: number; }

export interface TrackSectionPayload {
  id: string;
  length_km: number;
  track_type: TrackType;
  capacity_departures_per_hour: number;
  shared_group?: string | null;
  station_ids: string[];
  speed_limit_kph?: number | null;
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
    track_section_ids?: string[];
    both_ways?: boolean;
    closed?: boolean;
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
    departure_offset_by_period?: Record<string, number>;
    phase_by_period?: Record<string, number>;
  }>;
  track_sections: TrackSectionPayload[];
  stations: StationPayload[];
  platforms: PlatformPayload[];
  station_groups: Array<{ id: string; name: string; station_ids: string[]; transfer_walk_min: number }>;
  rolling_stock: RollingStockPayload[];
  fare_groups: FareGroupPayload[];
}

export interface ValidationResult {
  valid: boolean;
  errors: string[];
}
