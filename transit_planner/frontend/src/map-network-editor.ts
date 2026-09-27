import type { Map as MapLibreMap, MapMouseEvent } from "maplibre-gl";
import type { GeoJSONSource } from "maplibre-gl";
import type { NetworkPayload } from "./types";
import { networkToGeoJSON } from "./network-editor";
import { lonLatToLocalMeters, normalizeTrackSection } from "./core/geometry";
import { CommandHistory, type Command } from "./core/history";

export type MapEditorMode = "select" | "node" | "track";

export interface MapNetworkEditorOptions {
  getNetwork: () => NetworkPayload;
  setNetwork: (network: NetworkPayload) => void;
  getOrigin: () => { lon: number; lat: number };
  markDirty?: () => void;
  onSelection?: (kind: "node" | "track" | null, id: string | null) => void;
}

export class MapNetworkEditor {
  private mode: MapEditorMode = "select";
  private pendingNodeId: string | null = null;
  private selectedNodeId: string | null = null;
  private selectedTrackId: string | null = null;
  private draggingNodeId: string | null = null;
  private dragStartNetwork: NetworkPayload | null = null;
  private readonly history = new CommandHistory<NetworkPayload>();

  constructor(private readonly map: MapLibreMap, private readonly options: MapNetworkEditorOptions) {
    this.ensureLayers();
    this.map.on("click", this.onClick);
    this.map.on("mousemove", this.onMove);
    this.map.on("mousedown", "network-nodes", this.onMouseDown);
    this.map.on("mouseup", this.onMouseUp);
    this.map.on("mouseleave", this.onMouseUp);
    this.map.on("mouseenter", "network-nodes", () => { this.map.getCanvas().style.cursor = "pointer"; });
    this.map.on("mouseleave", "network-nodes", () => { this.map.getCanvas().style.cursor = ""; });
    this.map.on("mouseenter", "network-tracks", () => { this.map.getCanvas().style.cursor = "pointer"; });
    this.map.on("mouseleave", "network-tracks", () => { this.map.getCanvas().style.cursor = ""; });
    this.refresh();
  }

  setMode(mode: MapEditorMode): void {
    this.mode = mode;
    this.pendingNodeId = null;
    this.map.getCanvas().style.cursor = mode === "node" || mode === "track" ? "crosshair" : "";
  }

  getMode(): MapEditorMode { return this.mode; }
  canUndo(): boolean { return this.history.canUndo; }
  canRedo(): boolean { return this.history.canRedo; }
  undo(): void { try { this.options.setNetwork(this.history.undo(this.options.getNetwork())); this.refresh(); } catch {} }
  redo(): void { try { this.options.setNetwork(this.history.redo(this.options.getNetwork())); this.refresh(); } catch {} }
  applyNetwork(next: NetworkPayload, label = "Изменение сети"): void { this.commit(next, label); }

  updateTrack(trackId: string, patch: Partial<NetworkPayload["track_sections"][number]>): void {
    const next = structuredClone(this.options.getNetwork());
    const track = next.track_sections.find(item => item.id === trackId);
    if (!track) throw new Error("Участок не найден: " + trackId);
    Object.assign(track, patch);
    const nodes = new Map(next.track_nodes.map(node => [node.id, node]));
    next.track_sections = next.track_sections.map(item => item.id === trackId ? normalizeTrackSection(track, nodes) : item);
    this.commit(next, "Изменение участка " + trackId);
  }

  deleteTrack(trackId: string): void {
    const next = structuredClone(this.options.getNetwork());
    if (!next.track_sections.some(item => item.id === trackId)) return;
    next.track_sections = next.track_sections.filter(item => item.id !== trackId);
    next.crossovers = next.crossovers.filter(item => item.from_track_id !== trackId && item.to_track_id !== trackId);
    next.signal_blocks = next.signal_blocks.filter(item => item.track_section_id !== trackId);
    next.routes = next.routes.map(route => ({ ...route, track_section_ids: route.track_section_ids?.filter(id => id !== trackId) }));
    this.commit(next, "Удаление участка " + trackId);
  }

  deleteNode(nodeId: string): void {
    const next = structuredClone(this.options.getNetwork());
    if (next.track_sections.some(item => item.start_node_id === nodeId || item.end_node_id === nodeId)) throw new Error("Нельзя удалить узел, связанный с участком");
    if (!next.track_nodes.some(item => item.id === nodeId)) return;
    next.track_nodes = next.track_nodes.filter(item => item.id !== nodeId);
    this.commit(next, "Удаление узла " + nodeId);
  }


  private commit(next: NetworkPayload, label: string): void {
    const before = structuredClone(this.options.getNetwork());
    const after = structuredClone(next);
    const command: Command<NetworkPayload> = { label, execute: () => after, undo: () => before };
    this.options.setNetwork(this.history.execute(command, before));
    this.options.markDirty?.();
    this.refresh();
  }

  refresh(): void {
    const source = this.map.getSource("network-editor") as GeoJSONSource | undefined;
    if (source) source.setData(networkToGeoJSON(this.options.getNetwork()) as never);
    this.refreshSelection();
  }

  private ensureLayers(): void {
    if (!this.map.getSource("network-editor")) {
      this.map.addSource("network-editor", { type: "geojson", data: networkToGeoJSON(this.options.getNetwork()) as never });
    }
    if (!this.map.getLayer("network-tracks")) {
      this.map.addLayer({
        id: "network-tracks", type: "line", source: "network-editor",
        filter: ["==", ["get", "kind"], "track"],
        paint: { "line-color": "#2563eb", "line-width": 5, "line-opacity": 0.85 },
      });
    }
    if (!this.map.getLayer("network-nodes")) {
      this.map.addLayer({
        id: "network-nodes", type: "circle", source: "network-editor",
        filter: ["==", ["get", "kind"], "node"],
        paint: { "circle-radius": 7, "circle-color": "#ffffff", "circle-stroke-color": "#2563eb", "circle-stroke-width": 2 },
      });
    }
  }

  private coordinateToLocal(e: MapMouseEvent): { x: number; y: number } {
    const o = this.options.getOrigin();
    return lonLatToLocalMeters(e.lngLat.lng, e.lngLat.lat, o.lon, o.lat);
  }

  private onClick = (e: MapMouseEvent): void => {
    if (this.mode === "node") {
      const p = this.coordinateToLocal(e);
      const network = structuredClone(this.options.getNetwork());
      const id = "node-" + crypto.randomUUID().slice(0, 8);
      network.track_nodes.push({ id, x: p.x, y: p.y, elevation_m: 0 });
      this.commit(network, "Добавить узел");
      return;
    }

    const hits = this.map.queryRenderedFeatures(e.point, { layers: ["network-nodes", "network-tracks"] });
    const hit = hits[0];
    if (!hit) {
      this.pendingNodeId = null;
      this.setSelection(null, null);
      return;
    }
    const id = String(hit.properties?.id ?? "");
    const kind = String(hit.properties?.kind ?? "");
    if (this.mode === "track") {
      if (kind !== "node") return;
      if (!this.pendingNodeId) {
        this.pendingNodeId = id;
        this.setSelection("node", id);
        return;
      }
      if (this.pendingNodeId === id) return;
      const network = structuredClone(this.options.getNetwork());
      const nodes = new Map(network.track_nodes.map(n => [n.id, n]));
      const a = nodes.get(this.pendingNodeId);
      const b = nodes.get(id);
      if (!a || !b) return;
      const dx = b.x - a.x, dy = b.y - a.y;
      const length = Math.max(0.001, Math.hypot(dx, dy) / 1000);
      const trackId = "track-" + crypto.randomUUID().slice(0, 8);
      network.track_sections.push({
        id: trackId, length_km: length, track_type: "surface",
        capacity_departures_per_hour: 30, station_ids: [], speed_limit_kph: 50,
        start_node_id: a.id, end_node_id: b.id, start_elevation_m: a.elevation_m,
        end_elevation_m: b.elevation_m, max_slope_percent: 6, curve_radius_m: null,
        track_count: 1, direction: "both", parallel_group: null,
        grade_crossing_count: 0, elevation_delta_m: b.elevation_m - a.elevation_m,
        slope_percent: (b.elevation_m - a.elevation_m) / Math.max(0.001, Math.hypot(dx, dy)) * 100,
      });
      this.commit(network, "Добавить участок");
      this.pendingNodeId = id;
      this.setSelection("track", trackId);
      return;
    }

    this.setSelection(kind === "node" || kind === "track" ? kind : null, id);
  };

  private onMouseDown = (e: MapMouseEvent): void => {
    if (this.mode !== "select") return;
    const hits = this.map.queryRenderedFeatures(e.point, { layers: ["network-nodes"] });
    const hit = hits[0];
    const id = String(hit?.properties?.id ?? "");
    if (!id) return;
    this.draggingNodeId = id;
    this.dragStartNetwork = structuredClone(this.options.getNetwork());
    this.map.dragPan.disable();
    e.preventDefault();
  };

  private onMove = (e: MapMouseEvent): void => {
    if (!this.draggingNodeId || !e.originalEvent.buttons) return;
    const p = this.coordinateToLocal(e);
    const next = structuredClone(this.options.getNetwork());
    const node = next.track_nodes.find(n => n.id === this.draggingNodeId);
    if (!node) return;
    node.x = p.x; node.y = p.y;
    const nodes = new Map(next.track_nodes.map(n => [n.id, n]));
    next.track_sections = next.track_sections.map(section => section.start_node_id === node.id || section.end_node_id === node.id
      ? normalizeTrackSection(section, nodes) : section);
    this.options.setNetwork(next);
    this.refresh();
  };

  private onMouseUp = (): void => {
    if (!this.draggingNodeId) return;
    const id = this.draggingNodeId;
    const before = this.dragStartNetwork;
    const after = structuredClone(this.options.getNetwork());
    this.draggingNodeId = null;
    this.dragStartNetwork = null;
    this.map.dragPan.enable();
    if (before && JSON.stringify(before) !== JSON.stringify(after)) {
      const command: Command<NetworkPayload> = { label: "Переместить узел " + id, execute: () => after, undo: () => before };
      this.options.setNetwork(this.history.execute(command, before));
      this.options.markDirty?.();
      this.refresh();
    }
  };

  private setSelection(kind: "node" | "track" | null, id: string | null): void {
    this.selectedNodeId = kind === "node" ? id : null;
    this.selectedTrackId = kind === "track" ? id : null;
    this.options.onSelection?.(kind, id);
    this.refreshSelection();
  }

  private refreshSelection(): void {
    if (!this.map.getLayer("network-tracks") || !this.map.getLayer("network-nodes")) return;
    this.map.setPaintProperty("network-tracks", "line-color",
      ["case", ["==", ["get", "id"], this.selectedTrackId ?? ""], "#f59e0b", "#2563eb"]);
    this.map.setPaintProperty("network-nodes", "circle-color",
      ["case", ["==", ["get", "id"], this.selectedNodeId ?? ""], "#f59e0b", "#ffffff"]);
  }

  dispose(): void {
    this.map.off("click", this.onClick);
    this.map.off("mousemove", this.onMove);
    this.map.off("mousedown", "network-nodes", this.onMouseDown);
    this.map.off("mouseup", this.onMouseUp);
    this.map.off("mouseleave", this.onMouseUp);
  }
}
