import type { NetworkPayload, TrackNodePayload, TrackSectionPayload } from "../types";
import { normalizeTrackSection } from "./geometry";
import { CommandHistory, type Command } from "./history";

export interface NetworkEditorState { network: NetworkPayload; selectedNodeId: string | null; selectedTrackId: string | null; }

function cloneNetwork(network: NetworkPayload): NetworkPayload {
  return structuredClone(network);
}

export class NetworkEditor {
  readonly history = new CommandHistory<NetworkEditorState>();
  private state: NetworkEditorState;

  constructor(network: NetworkPayload) {
    this.state = { network: cloneNetwork(network), selectedNodeId: null, selectedTrackId: null };
  }

  snapshot(): NetworkEditorState { return structuredClone(this.state); }
  get network(): NetworkPayload { return this.state.network; }
  selectNode(id: string | null): void { this.state.selectedNodeId = id; this.state.selectedTrackId = null; }
  selectTrack(id: string | null): void { this.state.selectedTrackId = id; this.state.selectedNodeId = null; }

  private apply(command: Command<NetworkEditorState>): NetworkEditorState {
    this.state = this.history.execute(command, this.state);
    return this.snapshot();
  }

  moveNode(nodeId: string, x: number, y: number, elevation_m?: number): NetworkEditorState {
    const before = this.snapshot();
    const after = this.snapshot();
    const node = after.network.track_nodes.find(n => n.id === nodeId);
    if (!node) return before;
    node.x = x; node.y = y;
    if (elevation_m !== undefined) node.elevation_m = elevation_m;
    const nodes = new Map(after.network.track_nodes.map(n => [n.id, n]));
    after.network.track_sections = after.network.track_sections.map(s => normalizeTrackSection(s, nodes));
    return this.apply({
      label: "move node " + nodeId,
      execute: () => after,
      undo: () => before,
    });
  }

  addNode(node: TrackNodePayload): NetworkEditorState {
    const before = this.snapshot();
    const after = this.snapshot();
    if (after.network.track_nodes.some(n => n.id === node.id)) throw new Error("Node already exists: " + node.id);
    after.network.track_nodes.push(node);
    return this.apply({ label: "add node " + node.id, execute: () => after, undo: () => before });
  }

  deleteNode(nodeId: string): NetworkEditorState {
    const before = this.snapshot();
    const after = this.snapshot();
    if (after.network.track_sections.some(s => s.start_node_id === nodeId || s.end_node_id === nodeId)) {
      throw new Error("Cannot delete a node referenced by a track section");
    }
    after.network.track_nodes = after.network.track_nodes.filter(n => n.id !== nodeId);
    return this.apply({ label: "delete node " + nodeId, execute: () => after, undo: () => before });
  }

  addTrack(section: TrackSectionPayload): NetworkEditorState {
    const before = this.snapshot();
    const after = this.snapshot();
    if (after.network.track_sections.some(s => s.id === section.id)) throw new Error("Track already exists: " + section.id);
    const nodes = new Map(after.network.track_nodes.map(n => [n.id, n]));
    after.network.track_sections.push(normalizeTrackSection(section, nodes));
    return this.apply({ label: "add track " + section.id, execute: () => after, undo: () => before });
  }

  updateTrack(trackId: string, patch: Partial<TrackSectionPayload>): NetworkEditorState {
    const before = this.snapshot();
    const after = this.snapshot();
    const index = after.network.track_sections.findIndex(s => s.id === trackId);
    if (index < 0) throw new Error("Track not found: " + trackId);
    const nodes = new Map(after.network.track_nodes.map(n => [n.id, n]));
    after.network.track_sections[index] = normalizeTrackSection({ ...after.network.track_sections[index], ...patch }, nodes);
    return this.apply({ label: "edit track " + trackId, execute: () => after, undo: () => before });
  }

  deleteTrack(trackId: string): NetworkEditorState {
    const before = this.snapshot();
    const after = this.snapshot();
    after.network.track_sections = after.network.track_sections.filter(s => s.id !== trackId);
    after.network.crossovers = after.network.crossovers.filter(c => c.from_track_id !== trackId && c.to_track_id !== trackId);
    after.network.signal_blocks = after.network.signal_blocks.filter(b => b.track_section_id !== trackId);
    after.network.routes = after.network.routes.map(r => ({ ...r, track_section_ids: r.track_section_ids?.filter(id => id !== trackId) }));
    return this.apply({ label: "delete track " + trackId, execute: () => after, undo: () => before });
  }

  splitTrack(trackId: string, ratio = 0.5): NetworkEditorState {
    const before = this.snapshot();
    const after = this.snapshot();
    const track = after.network.track_sections.find(t => t.id === trackId);
    if (!track || !track.start_node_id || !track.end_node_id) throw new Error("Track endpoints are required");
    const a = after.network.track_nodes.find(n => n.id === track.start_node_id);
    const b = after.network.track_nodes.find(n => n.id === track.end_node_id);
    if (!a || !b) throw new Error("Track endpoints not found");
    const r = Math.max(0.05, Math.min(0.95, ratio));
    const nodeId = trackId + "-split";
    if (after.network.track_nodes.some(n => n.id === nodeId)) throw new Error("Split node already exists");
    after.network.track_nodes.push({ id: nodeId, x: a.x + (b.x-a.x)*r, y: a.y + (b.y-a.y)*r, elevation_m: a.elevation_m + (b.elevation_m-a.elevation_m)*r });
    const first = { ...track, id: trackId + "-a", end_node_id: nodeId };
    const second = { ...track, id: trackId + "-b", start_node_id: nodeId };
    const nodes = new Map(after.network.track_nodes.map(n => [n.id, n]));
    after.network.track_sections = after.network.track_sections.flatMap(t => t.id === trackId ? [normalizeTrackSection(first,nodes), normalizeTrackSection(second,nodes)] : [t]);
    after.network.routes = after.network.routes.map(route => ({ ...route, track_section_ids: route.track_section_ids?.flatMap(id => id === trackId ? [first.id, second.id] : [id]) }));
    return this.apply({ label: "split track " + trackId, execute: () => after, undo: () => before });
  }

  mergeTracks(firstId: string, secondId: string): NetworkEditorState {
    const before = this.snapshot();
    const after = this.snapshot();
    const a = after.network.track_sections.find(t => t.id === firstId);
    const b = after.network.track_sections.find(t => t.id === secondId);
    if (!a || !b || a.end_node_id !== b.start_node_id) throw new Error("Tracks must be connected end-to-start");
    const merged = normalizeTrackSection({ ...a, id: firstId + "-merged", end_node_id: b.end_node_id, length_km: a.length_km + b.length_km }, new Map(after.network.track_nodes.map(n => [n.id,n])));
    after.network.track_sections = after.network.track_sections.filter(t => t.id !== firstId && t.id !== secondId);
    after.network.track_sections.push(merged);
    after.network.routes = after.network.routes.map(route => ({ ...route, track_section_ids: route.track_section_ids?.flatMap(id => id === firstId || id === secondId ? [merged.id] : [id]) }));
    return this.apply({ label: "merge tracks", execute: () => after, undo: () => before });
  }

  undo(): NetworkEditorState { this.state = this.history.undo(this.state); return this.snapshot(); }
  redo(): NetworkEditorState { this.state = this.history.redo(this.state); return this.snapshot(); }
}
