import type { NetworkPayload } from "./types";

export interface NetworkIssue {
  severity: "error" | "warning";
  code: string;
  message: string;
  entityId?: string;
}

export function validateNetworkClient(network: NetworkPayload): NetworkIssue[] {
  const issues: NetworkIssue[] = [];
  const nodeIds = new Set<string>();

  for (const node of network.track_nodes) {
    if (nodeIds.has(node.id)) issues.push({ severity: "error", code: "DUPLICATE_NODE", message: "Duplicate track node", entityId: node.id });
    nodeIds.add(node.id);
    if (!Number.isFinite(node.x) || !Number.isFinite(node.y)) issues.push({ severity: "error", code: "INVALID_NODE_GEOMETRY", message: "Node coordinates are not finite", entityId: node.id });
  }

  const trackIds = new Set<string>();
  for (const track of network.track_sections) {
    if (trackIds.has(track.id)) issues.push({ severity: "error", code: "DUPLICATE_TRACK", message: "Duplicate track section", entityId: track.id });
    trackIds.add(track.id);
    if (!track.start_node_id || !nodeIds.has(track.start_node_id)) issues.push({ severity: "error", code: "MISSING_START_NODE", message: "Track start node is missing", entityId: track.id });
    if (!track.end_node_id || !nodeIds.has(track.end_node_id)) issues.push({ severity: "error", code: "MISSING_END_NODE", message: "Track end node is missing", entityId: track.id });
    if (track.length_km <= 0) issues.push({ severity: "error", code: "INVALID_LENGTH", message: "Track length must be positive", entityId: track.id });
    if ((track.track_count ?? 1) < 1) issues.push({ severity: "error", code: "INVALID_TRACK_COUNT", message: "Track count must be at least one", entityId: track.id });
  }
  return issues;
}
