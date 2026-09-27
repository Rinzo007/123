import type { TrackNodePayload, TrackSectionPayload } from "../types";

const EARTH_RADIUS_M = 6378137;

export function localMetersToLonLat(x: number, y: number, originLon: number, originLat: number): [number, number] {
  const cosLat = Math.cos(originLat * Math.PI / 180);
  return [
    originLon + (x / Math.max(1e-9, EARTH_RADIUS_M * cosLat)) * 180 / Math.PI,
    originLat + (y / EARTH_RADIUS_M) * 180 / Math.PI,
  ];
}

export function lonLatToLocalMeters(lon: number, lat: number, originLon: number, originLat: number): { x: number; y: number } {
  const cosLat = Math.cos(originLat * Math.PI / 180);
  return {
    x: ((lon - originLon) * Math.PI / 180) * EARTH_RADIUS_M * cosLat,
    y: ((lat - originLat) * Math.PI / 180) * EARTH_RADIUS_M,
  };
}

export function distanceMeters(a: TrackNodePayload, b: TrackNodePayload): number {
  return Math.hypot(b.x - a.x, b.y - a.y);
}

export function sectionLengthKm(section: TrackSectionPayload, nodes: Map<string, TrackNodePayload>): number {
  const start = section.start_node_id ? nodes.get(section.start_node_id) : undefined;
  const end = section.end_node_id ? nodes.get(section.end_node_id) : undefined;
  if (!start || !end) return section.length_km;
  return Math.max(0.001, distanceMeters(start, end) / 1000);
}

export function sectionSlopePercent(section: TrackSectionPayload, nodes: Map<string, TrackNodePayload>): number {
  const start = section.start_node_id ? nodes.get(section.start_node_id) : undefined;
  const end = section.end_node_id ? nodes.get(section.end_node_id) : undefined;
  if (!start || !end) return 0;
  const lengthM = Math.max(0.001, distanceMeters(start, end));
  return ((end.elevation_m - start.elevation_m) / lengthM) * 100;
}

export function normalizeTrackSection(section: TrackSectionPayload, nodes: Map<string, TrackNodePayload>): TrackSectionPayload {
  return {
    ...section,
    length_km: sectionLengthKm(section, nodes),
    slope_percent: sectionSlopePercent(section, nodes),
    start_elevation_m: section.start_node_id ? nodes.get(section.start_node_id)?.elevation_m ?? section.start_elevation_m : section.start_elevation_m,
    end_elevation_m: section.end_node_id ? nodes.get(section.end_node_id)?.elevation_m ?? section.end_elevation_m : section.end_elevation_m,
  };
}
