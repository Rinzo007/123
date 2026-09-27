import type { NetworkPayload } from "../types";

export type TopologySeverity = "error" | "warning";
export interface TopologyIssue { severity: TopologySeverity; code: string; message: string; objectId?: string; }

export function validateTopology(network: NetworkPayload): TopologyIssue[] {
  const issues: TopologyIssue[] = [];
  const nodeIds = new Set(network.track_nodes.map(node => node.id));
  const trackIds = new Set(network.track_sections.map(track => track.id));

  for (const track of network.track_sections) {
    if (track.start_node_id && !nodeIds.has(track.start_node_id)) issues.push({ severity:"error", code:"missing_start_node", message:"Участок " + track.id + ": начальный узел не найден", objectId:track.id });
    if (track.end_node_id && !nodeIds.has(track.end_node_id)) issues.push({ severity:"error", code:"missing_end_node", message:"Участок " + track.id + ": конечный узел не найден", objectId:track.id });
    if (track.start_node_id && track.start_node_id === track.end_node_id) issues.push({ severity:"error", code:"self_loop", message:"Участок " + track.id + ": начало и конец совпадают", objectId:track.id });
    if (track.length_km <= 0 || !Number.isFinite(track.length_km)) issues.push({ severity:"error", code:"invalid_length", message:"Участок " + track.id + ": некорректная длина", objectId:track.id });
    if (track.capacity_departures_per_hour <= 0 || !Number.isFinite(track.capacity_departures_per_hour)) issues.push({ severity:"error", code:"invalid_capacity", message:"Участок " + track.id + ": некорректная пропускная способность", objectId:track.id });
    if ((track.track_count ?? 1) < 1) issues.push({ severity:"error", code:"invalid_track_count", message:"Участок " + track.id + ": число путей меньше 1", objectId:track.id });
  }

  for (const crossover of network.crossovers) {
    if (!trackIds.has(crossover.from_track_id) || !trackIds.has(crossover.to_track_id)) issues.push({ severity:"error", code:"dangling_crossover", message:"Стрелочный перевод " + crossover.id + " ссылается на отсутствующий участок", objectId:crossover.id });
    if (crossover.from_track_id === crossover.to_track_id) issues.push({ severity:"error", code:"self_crossover", message:"Стрелочный перевод " + crossover.id + ": одинаковые участки", objectId:crossover.id });
    if (crossover.position < 0 || crossover.position > 1) issues.push({ severity:"error", code:"invalid_crossover_position", message:"Стрелочный перевод " + crossover.id + ": позиция должна быть 0..1", objectId:crossover.id });
  }

  for (const block of network.signal_blocks) {
    if (!trackIds.has(block.track_section_id)) issues.push({ severity:"error", code:"dangling_signal_block", message:"Сигнальный блок " + block.id + " ссылается на отсутствующий участок", objectId:block.id });
    if (block.start_position < 0 || block.end_position > 1 || block.start_position >= block.end_position) issues.push({ severity:"error", code:"invalid_signal_block", message:"Сигнальный блок " + block.id + ": некорректные границы", objectId:block.id });
    if (block.minimum_headway_seconds <= 0) issues.push({ severity:"error", code:"invalid_headway", message:"Сигнальный блок " + block.id + ": headway должен быть положительным", objectId:block.id });
  }

  for (const route of network.routes) {
    for (const trackId of route.track_section_ids ?? []) {
      if (!trackIds.has(trackId)) issues.push({ severity:"error", code:"dangling_route_track", message:"Маршрут " + route.id + " ссылается на отсутствующий участок " + trackId, objectId:route.id });
    }
  }

  const degrees = new Map<string, number>();
  for (const track of network.track_sections) {
    if (track.start_node_id) degrees.set(track.start_node_id, (degrees.get(track.start_node_id) ?? 0) + 1);
    if (track.end_node_id) degrees.set(track.end_node_id, (degrees.get(track.end_node_id) ?? 0) + 1);
  }
  for (const node of network.track_nodes) if (!degrees.has(node.id)) issues.push({ severity:"warning", code:"isolated_node", message:"Узел " + node.id + " не связан ни с одним участком", objectId:node.id });

  return issues;
}
export function topologyIsValid(network: NetworkPayload): boolean {
  return validateTopology(network).every(issue => issue.severity !== "error");
}
