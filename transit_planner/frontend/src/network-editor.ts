import type { FeatureCollection, LineString, Point, Feature } from "./geojson";
import type { NetworkPayload, TrackSectionPayload } from "./types";
import { localMetersToLonLat } from "./core/geometry";

export type EditorTool = "select" | "node" | "track";

export interface NetworkFeatureProperties {
  id: string;
  kind: "track" | "node";
  track_type?: string;
  direction?: string;
  track_count?: number;
  speed_limit_kph?: number | null;
}

export function networkToGeoJSON(network: NetworkPayload): FeatureCollection<LineString | Point, NetworkFeatureProperties> {
  const nodes = new Map(network.track_nodes.map(n => [n.id, n]));
  const features: Array<Feature<LineString | Point, NetworkFeatureProperties>> = [];

  for (const section of network.track_sections) {
    const a = section.start_node_id ? nodes.get(section.start_node_id) : undefined;
    const b = section.end_node_id ? nodes.get(section.end_node_id) : undefined;
    if (!a || !b) continue;
    features.push({
      type: "Feature",
      geometry: { type: "LineString", coordinates: [localMetersToLonLat(a.x, a.y, network.origin_lon, network.origin_lat), localMetersToLonLat(b.x, b.y, network.origin_lon, network.origin_lat)] },
      properties: {
        id: section.id, kind: "track", track_type: section.track_type,
        direction: section.direction, track_count: section.track_count,
        speed_limit_kph: section.speed_limit_kph,
      },
    });
  }

  for (const node of network.track_nodes) {
    features.push({
      type: "Feature",
      geometry: { type: "Point", coordinates: localMetersToLonLat(node.x, node.y, network.origin_lon, network.origin_lat) },
      properties: { id: node.id, kind: "node" },
    });
  }
  return { type: "FeatureCollection", features };
}

export function createNode(network: NetworkPayload, id: string, x: number, y: number, elevation_m = 0): NetworkPayload {
  if (network.track_nodes.some(n => n.id === id)) throw new Error("Node already exists: " + id);
  return { ...network, track_nodes: [...network.track_nodes, { id, x, y, elevation_m }] };
}

export function createTrack(
  network: NetworkPayload,
  id: string,
  start_node_id: string,
  end_node_id: string,
): NetworkPayload {
  if (network.track_sections.some(s => s.id === id)) throw new Error("Track already exists: " + id);
  const nodes = new Map(network.track_nodes.map(n => [n.id, n]));
  const start = nodes.get(start_node_id);
  const end = nodes.get(end_node_id);
  if (!start || !end) throw new Error("Track endpoints must exist");
  const length = Math.max(0.001, Math.hypot(end.x - start.x, end.y - start.y) / 1000);
  const slope = ((end.elevation_m - start.elevation_m) / Math.max(0.001, Math.hypot(end.x - start.x, end.y - start.y))) * 100;
  const section: TrackSectionPayload = {
    id,
    length_km: length,
    track_type: "surface",
    capacity_departures_per_hour: 30,
    station_ids: [],
    speed_limit_kph: 50,
    start_node_id,
    end_node_id,
    start_elevation_m: start.elevation_m,
    end_elevation_m: end.elevation_m,
    max_slope_percent: 6,
    curve_radius_m: null,
    track_count: 1,
    direction: "both",
    parallel_group: null,
    grade_crossing_count: 0,
    elevation_delta_m: end.elevation_m - start.elevation_m,
    slope_percent: slope,
  };
  return { ...network, track_sections: [...network.track_sections, section] };
}
