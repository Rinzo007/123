import { Map as MapLibreMap, NavigationControl, type GeoJSONSource, type MapMouseEvent } from "maplibre-gl";
import type { FeatureCollection, LineString, Point as GeoJSONPoint } from "./geojson";
import {
  calculateAssignment,
  calculateCityAssignment,
  calculateEconomics,
  compareScenarios,
  createTimetable,
  loadDemandStreets,
  loadOvertureNetwork,
  loadOvertureGraph,
  loadPopulationZones,
  loadReferenceDemand,
  validateNetwork,
  type EconomicsResult,
  type OvertureNetworkResponse,
  type ScenarioPayload,
} from "./api";
import type { NetworkPayload, StopDraft, TransitMode, TrackType } from "./types";
import {
  loadBinaryDataset,
  loadDataset,
  loadProject,
  loadUiSettings,
  saveBinaryDataset,
  saveDataset,
  saveProject,
  saveUiSettings,
} from "./storage";
import { createEvaluationClient, disposeComputationWorkers, networkCounts } from "./workers";
import { runRuntimePreview } from "./workers/reference-runtime";
import { MapNetworkEditor, type MapEditorMode } from "./map-network-editor";
import { RouteEditor } from "./planning/route-editor";
import { estimateFleetRequirement } from "./planning/fleet";
import { generateServiceTimetable } from "./planning/timetable";
import { validateTopology } from "./core/topology";
import { decodeLines, encodeLines } from "./line-cache";
import {
  assembleRouteCoordinates,
  decodeStreets,
  nearestGraphNode,
  routeStreets,
  type StreetGraph,
} from "./street-graph";
import {
  changedSegments,
  keepNetwork,
  planningPreview,
  probe,
  segmentSignatures,
  type PlanningPreview,
} from "./simulation/preview";

const INITIAL_MAP_CENTER: [number, number] = [39.2, 51.67];
const MAP_STYLE = "https://tiles.openfreemap.org/styles/liberty";
const MODE_LABELS: Record<TransitMode, string> = {
  bus: "Автобус",
  tram: "Трамвай",
  metro: "Метро",
  rail: "Железная дорога",
};
const MODE_CAPACITY: Record<TransitMode, number> = { bus: 90, tram: 250, metro: 750, rail: 1000 };
const PERIODS = [
  { id: "early", start_minute: 240, end_minute: 360 },
  { id: "am", start_minute: 360, end_minute: 540 },
  { id: "mid", start_minute: 540, end_minute: 900 },
  { id: "pm", start_minute: 900, end_minute: 1140 },
  { id: "eve", start_minute: 1140, end_minute: 1440 },
] as const;

type Bounds = { south: number; west: number; north: number; east: number };
type ProjectFile = {
  format: "transit-planner-project";
  version: number;
  routeName: string;
  mode: TransitMode;
  headways: Record<string, number>;
  stops: StopDraft[];
  network: NetworkPayload;
  roadRoute?: FeatureCollection<LineString, object> | null;
  previewTrips?: number;
  economics?: { farePerTransitTrip?: number; annualDays?: number };
  scenarioBase?: {
    network: NetworkPayload;
    origin: { lon: number; lat: number };
    destination: { lon: number; lat: number };
    trips: number;
    farePerTransitTrip: number;
    annualDays: number;
  } | null;
};

const root = document.getElementById("app");
if (!root) throw new Error("Корневой элемент приложения не найден");

let map: MapLibreMap | null = null;
let mapReady = false;
let busy = false;
let drawMode = false;
let viewMode: "map" | "network" = "map";
let initialized = false;
let previewTrips = 1000;
let farePerTransitTrip = 0;
let annualDays = 365;
let routeName = "Новый маршрут";
let mode: TransitMode = "bus";
let headways: Record<string, number> = { early: 20, am: 10, mid: 12, pm: 10, eve: 20 };
let stops: StopDraft[] = [];
let roadRoute: FeatureCollection<LineString, object> | null = null;
let streetGraph: StreetGraph | null = null;
let cityRoads: FeatureCollection | null = null;
let cityConnectors: FeatureCollection | null = null;
let cityStops: FeatureCollection | null = null;
let cityPlaces: FeatureCollection | null = null;
let populationZones: FeatureCollection | null = null;
let demandStreets: FeatureCollection | null = null;
let assignmentResult: Awaited<ReturnType<typeof calculateAssignment>> | null = null;
let cityAssignmentMeta: Awaited<ReturnType<typeof calculateCityAssignment>>["data"] | null = null;
let cityAssignmentPeriods: Awaited<ReturnType<typeof calculateCityAssignment>>["periods"] = [];
let economicsResult: { scenario_id: string; name: string; economics: EconomicsResult } | null = null;
let scenarioBase: ProjectFile["scenarioBase"] = null;
let scenarioComparison: Awaited<ReturnType<typeof compareScenarios>> | null = null;
let timetable: Awaited<ReturnType<typeof createTimetable>> | null = null;
let evaluationSummary = { lines: 0, stops: 0, dailyDepartures: 0 };
let message = "Готово к редактированию";
let projectRevision = 0;
let autosaveTimer: ReturnType<typeof setTimeout> | null = null;
let previousSegmentSignatures: Map<string, string> | null = null;
let lastPlanningPreview: PlanningPreview | null = null;
let lastProbeDelta: ReturnType<typeof probe>["delta"] | null = null;
const cpVariant = new URLSearchParams(location.search).get("cp-variant") || "control";
let evaluationClient: ReturnType<typeof createEvaluationClient> | null = null;
let showRoads = true;
let showRoadSpeed = false;
let showStops = true;
let showPlaces = true;
let showConnectors = false;
let showPassengerFlow = true;
let showStationLoads = true;
let showDemandStreets = true;
let showPopulation = false;

document.documentElement.dataset.cpVariant = cpVariant;

function setEditorMode(mode: MapEditorMode): void { editorMode = mode; mapNetworkEditor?.setMode(mode); setStatus(mode === "select" ? "Выбор объектов сети" : mode === "node" ? "Добавление узлов" : mode === "track" ? "Создание участка: выберите два узла" : mode === "crossover" ? "Стрелочный перевод: выберите два участка" : "Сигнальный блок: выберите участок"); }

const shell = document.createElement("div");
shell.className = "app-shell";
shell.innerHTML = `
  <header class="topbar" role="banner">
    <div>
      <div class="brand">Transit Planner</div>
      <div class="subtitle">Проектирование транспортной сети</div>
      <div id="save-state" class="save-state" aria-live="polite">Сохранено</div>
    </div>
    <div class="actions" role="toolbar" aria-label="Действия">
      <button data-action="view-map" class="active-toggle">Карта</button>
      <button data-action="view-network">Сеть</button><button data-action="editor-select">Выбор</button><button data-action="editor-node">Узел</button><button data-action="editor-track">Участок</button><button data-action="editor-crossover">Стрелка</button><button data-action="editor-signal">Сигнал</button><button data-action="split-track">Разделить</button><button data-action="merge-track">Объединить</button><button data-action="undo" title="Ctrl+Z">↶</button><button data-action="redo" title="Ctrl+Shift+Z">↷</button>
      <button data-action="load-city">Загрузить Overture</button>
      <button data-action="build-road" disabled>Построить по дорогам</button>
      <button data-action="draw" class="primary" aria-pressed="false">Добавить остановки</button>
      <button data-action="validate" disabled>Проверить сеть</button>
      <button data-action="capture-base" disabled>Зафиксировать базовый</button>
      <button data-action="compare" disabled>Сравнить</button>
      <button data-action="save">Сохранить</button>
      <button data-action="open">Открыть</button>
      <input id="file-input" type="file" accept="application/json" hidden />
    </div>
  </header>
  <div class="workspace">
    <aside class="sidebar" aria-label="Параметры сети">
      <section>
        <div class="section-title">Маршрут</div>
        <label>Название<input id="route-name" /></label>
        <label>Вид транспорта<select id="mode"></select></label>
        <div class="preview-demand">
          <div class="section-title">Проверочный расчёт</div>
          <label>Спрос, поездок/сутки<input id="preview-trips" type="number" min="1" max="100000" /></label>
          <button data-action="planning-preview" disabled>Быстрый расчёт</button>
      <button data-action="preview" class="primary" disabled>Рассчитать пассажиропоток</button>
          <button data-action="economics" disabled>Рассчитать экономику</button>
          <button data-action="city-assignment" disabled>Рассчитать городскую сеть</button>
          <label>Тариф за поездку<input id="fare" type="number" min="0" step="0.01" /></label>
          <label>Дней в году<input id="annual-days" type="number" min="1" max="366" /></label>
        </div>
        <div class="period-headways">
          <div class="section-title">Интервалы</div>
          <div id="headways"></div>
        </div>
      </section>
      <section class="stops-panel">
        <div class="section-title">Остановки <span id="stop-count">(0)</span></div>
        <div id="stop-list" class="stop-list"></div>
      </section>
      <section>
        <div class="section-title">Слои</div>
        <label class="check-row"><input id="show-roads" type="checkbox" /> Дороги</label>
        <label class="check-row"><input id="show-road-speed" type="checkbox" /> Скорости дорог</label>
        <label class="check-row"><input id="show-stops" type="checkbox" /> Остановки Overture</label>
        <label class="check-row"><input id="show-places" type="checkbox" /> Places Overture</label>
        <label class="check-row"><input id="show-connectors" type="checkbox" /> Connectors</label>
        <label class="check-row"><input id="show-population" type="checkbox" /> WorldPop</label>
        <label class="check-row"><input id="show-demand-streets" type="checkbox" /> Demand streets</label>
        <label class="check-row"><input id="show-passenger-flow" type="checkbox" /> Пассажиропоток</label>
        <label class="check-row"><input id="show-station-loads" type="checkbox" /> Нагрузка остановок</label>
      </section>
      <footer id="status" aria-live="polite">Готово</footer>
    </aside>
    <main class="map-area" id="map-area" aria-label="Карта">
      <div id="map" class="map"></div>
      <div id="map-hint" class="map-hint" hidden>Кликайте по карте, чтобы добавлять остановки</div>
      <button data-action="clear" class="clear-button">Очистить маршрут</button>
    </main>
    <aside id="property-panel" class="property-panel" aria-label="Свойства объекта"></aside>
    <main class="network-area" id="network-area" hidden>
      <div id="network-content" class="network-view"></div>
    </main>
  </div>
`;
root.appendChild(shell);

const mapElement = shell.querySelector<HTMLDivElement>("#map")!;
const statusElement = shell.querySelector<HTMLElement>("#status")!;
const saveStateElement = shell.querySelector<HTMLElement>("#save-state")!;
const networkArea = shell.querySelector<HTMLElement>("#network-area")!;
const mapArea = shell.querySelector<HTMLElement>("#map-area")!;
const networkContent = shell.querySelector<HTMLElement>("#network-content")!;
const propertyPanel = shell.querySelector<HTMLElement>("#property-panel")!;
let editorSelection: { kind: "node" | "track" | "crossover" | "signal" | null; id: string | null } = { kind: null, id: null };
const fileInput = shell.querySelector<HTMLInputElement>("#file-input")!;
const routeNameInput = shell.querySelector<HTMLInputElement>("#route-name")!;
const modeInput = shell.querySelector<HTMLSelectElement>("#mode")!;
const previewTripsInput = shell.querySelector<HTMLInputElement>("#preview-trips")!;
const fareInput = shell.querySelector<HTMLInputElement>("#fare")!;
const annualDaysInput = shell.querySelector<HTMLInputElement>("#annual-days")!;
const headwayContainer = shell.querySelector<HTMLElement>("#headways")!;
const stopListElement = shell.querySelector<HTMLElement>("#stop-list")!;
const stopCountElement = shell.querySelector<HTMLElement>("#stop-count")!;
const mapHint = shell.querySelector<HTMLElement>("#map-hint")!;

type SettingKey =
  | "showRoads"
  | "showRoadSpeed"
  | "showStops"
  | "showPlaces"
  | "showConnectors"
  | "showPopulation"
  | "showDemandStreets"
  | "showPassengerFlow"
  | "showStationLoads";

const toggleInputs: Array<[HTMLInputElement, SettingKey]> = [
  [shell.querySelector<HTMLInputElement>("#show-roads")!, "showRoads"],
  [shell.querySelector<HTMLInputElement>("#show-road-speed")!, "showRoadSpeed"],
  [shell.querySelector<HTMLInputElement>("#show-stops")!, "showStops"],
  [shell.querySelector<HTMLInputElement>("#show-places")!, "showPlaces"],
  [shell.querySelector<HTMLInputElement>("#show-connectors")!, "showConnectors"],
  [shell.querySelector<HTMLInputElement>("#show-population")!, "showPopulation"],
  [shell.querySelector<HTMLInputElement>("#show-demand-streets")!, "showDemandStreets"],
  [shell.querySelector<HTMLInputElement>("#show-passenger-flow")!, "showPassengerFlow"],
  [shell.querySelector<HTMLInputElement>("#show-station-loads")!, "showStationLoads"],
];

const initialSettings = {
  showRoads,
  showRoadSpeed,
  showStops,
  showPlaces,
  showConnectors,
  showPopulation,
  showDemandStreets,
  showPassengerFlow,
  showStationLoads,
};

function renderPropertyPanel(): void {
  const { kind, id } = editorSelection;
  if (!kind || !id) {
    propertyPanel.innerHTML = '<div class="property-empty"><strong>Свойства</strong><p>Выберите узел или участок сети на карте.</p></div>';
    return;
  }
  const current = network;
  const node = kind === "node" ? current.track_nodes.find(n => n.id === id) : undefined;
  const track = kind === "track" ? current.track_sections.find(t => t.id === id) : undefined;
  const crossover = kind === "crossover" ? current.crossovers.find(c => c.id === id) : undefined;
  const signal = kind === "signal" ? current.signal_blocks.find(b => b.id === id) : undefined;
  if (!node && !track && !crossover && !signal) {
    propertyPanel.innerHTML = '<div class="property-empty"><strong>Свойства</strong><p>Объект больше не существует.</p></div>';
    return;
  }
  if (crossover) {
    propertyPanel.innerHTML = `<div class="property-head"><div><span class="property-kind">СТРЕЛОЧНЫЙ ПЕРЕВОД</span><h3>${crossover.id}</h3></div><button data-property-action="delete" class="danger">Удалить</button></div><label>ID<input value="${crossover.id}" disabled></label><label>От<input value="${crossover.from_track_id}" disabled></label><label>На<input value="${crossover.to_track_id}" disabled></label><label>Позиция<input data-field="position" type="number" min="0" max="1" step="0.01" value="${crossover.position}"></label>`;
  } else if (signal) {
    propertyPanel.innerHTML = `<div class="property-head"><div><span class="property-kind">СИГНАЛЬНЫЙ БЛОК</span><h3>${signal.id}</h3></div><button data-property-action="delete" class="danger">Удалить</button></div><label>ID<input value="${signal.id}" disabled></label><label>Участок<input value="${signal.track_section_id}" disabled></label><label>Начало<input data-field="start_position" type="number" min="0" max="1" step="0.01" value="${signal.start_position}"></label><label>Конец<input data-field="end_position" type="number" min="0" max="1" step="0.01" value="${signal.end_position}"></label><label>Headway, с<input data-field="minimum_headway_seconds" type="number" min="1" step="1" value="${signal.minimum_headway_seconds}"></label>`;
  } else if (node) {
    propertyPanel.innerHTML = `
      <div class="property-head"><div><span class="property-kind">УЗЕЛ</span><h3>${node.id}</h3></div><button data-property-action="delete" class="danger">Удалить</button></div>
      <label>ID<input value="${node.id}" disabled></label>
      <label>X, м<input data-field="x" type="number" step="0.01" value="${node.x}"></label>
      <label>Y, м<input data-field="y" type="number" step="0.01" value="${node.y}"></label>
      <label>Отметка, м<input data-field="elevation_m" type="number" step="0.1" value="${node.elevation_m}"></label>
      <div class="property-meta">Связанных участков: ${current.track_sections.filter(t => t.start_node_id === id || t.end_node_id === id).length}</div>`;
  } else if (track) {
    propertyPanel.innerHTML = `
      <div class="property-head"><div><span class="property-kind">УЧАСТОК</span><h3>${track.id}</h3></div><button data-property-action="delete" class="danger">Удалить</button></div>
      <label>ID<input value="${track.id}" disabled></label>
      <label>Тип пути<select data-field="track_type"><option value="surface">Поверхность</option><option value="elevated">Эстакада</option><option value="tunnel">Тоннель</option><option value="trenched">Выемка</option><option value="ramp">Рампа</option></select></label>
      <label>Направление<select data-field="direction"><option value="forward">Прямое</option><option value="reverse">Обратное</option><option value="both">Оба</option></select></label>
      <label>Пропускная способность, отп./ч<input data-field="capacity_departures_per_hour" type="number" min="1" step="1" value="${track.capacity_departures_per_hour}"></label>
      <label>Скорость, км/ч<input data-field="speed_limit_kph" type="number" min="1" step="1" value="${track.speed_limit_kph}"></label>
      <label>Путей<input data-field="track_count" type="number" min="1" step="1" value="${track.track_count}"></label>
      <label>Макс. уклон, %<input data-field="max_slope_percent" type="number" min="0" step="0.1" value="${track.max_slope_percent}"></label>
      <label>Радиус кривой, м<input data-field="curve_radius_m" type="number" min="0" step="1" value="${track.curve_radius_m ?? ""}"></label>
      <label>Переездов<input data-field="grade_crossing_count" type="number" min="0" step="1" value="${track.grade_crossing_count}"></label>
      <label>Группа параллельности<input data-field="parallel_group" value="${track.parallel_group ?? ""}"></label>
      <div class="property-grid"><div><span>Длина</span><b>${track.length_km.toFixed(3)} км</b></div><div><span>Уклон</span><b>${(track.slope_percent ?? 0).toFixed(2)}%</b></div><div><span>Перепад</span><b>${((track.end_elevation_m ?? 0) - (track.start_elevation_m ?? 0)).toFixed(1)} м</b></div><div><span>Пропускная способность</span><b>${track.capacity_departures_per_hour} отп./ч</b></div></div>`;
    const type = propertyPanel.querySelector<HTMLSelectElement>('[data-field="track_type"]'); if (type) type.value = track.track_type;
    const direction = propertyPanel.querySelector<HTMLSelectElement>('[data-field="direction"]'); if (direction) direction.value = track.direction ?? "both";
  }
  propertyPanel.querySelectorAll<HTMLInputElement | HTMLSelectElement>("[data-field]").forEach(input => {
    input.addEventListener("change", () => updateSelectedProperty(input.dataset.field!, input.value));
  });
  propertyPanel.querySelector('[data-property-action="delete"]')?.addEventListener("click", deleteSelectedProperty);
}
function updateSelectedProperty(field: string, raw: string): void {
  const next = structuredClone(network);
  if (editorSelection.kind === "node") {
    const item = next.track_nodes.find(n => n.id === editorSelection.id); if (!item) return;
    const value = field === "elevation_m" || field === "x" || field === "y" ? Number(raw) : raw;
    (item as any)[field] = value;
  } else if (editorSelection.kind === "crossover") {
    const item = next.crossovers.find(c => c.id === editorSelection.id); if (!item) return;
    if (field === "position") item.position = clampNumber(Number(raw), 0, 1);
  } else if (editorSelection.kind === "signal") {
    const item = next.signal_blocks.find(b => b.id === editorSelection.id); if (!item) return;
    if (field === "start_position" || field === "end_position" || field === "minimum_headway_seconds") (item as unknown as Record<string, number>)[field] = Number(raw);
  } else if (editorSelection.kind === "track") {
    const item = next.track_sections.find(t => t.id === editorSelection.id); if (!item) return;
    const numeric = ["capacity_departures_per_hour","speed_limit_kph","track_count","max_slope_percent","grade_crossing_count","curve_radius_m"].includes(field);
    (item as any)[field] = numeric ? (raw === "" && field === "curve_radius_m" ? null : Number(raw)) : raw;
    const a = next.track_nodes.find(n => n.id === item.start_node_id), b = next.track_nodes.find(n => n.id === item.end_node_id);
    if (a && b) { const d = Math.max(.001, Math.hypot(b.x-a.x,b.y-a.y)); const delta = b.elevation_m-a.elevation_m; item.length_km=d/1000; item.elevation_delta_m=delta; item.slope_percent=delta/d*100; item.start_elevation_m=a.elevation_m; item.end_elevation_m=b.elevation_m; }
  }
  if (mapNetworkEditor) mapNetworkEditor.applyNetwork(next, "Изменение свойств"); else { network = next; markDirty(); }
  renderPropertyPanel(); render();
}
function deleteSelectedProperty(): void {
  if (!editorSelection.kind || !editorSelection.id) return;
  try {
    if (mapNetworkEditor) {
      if (editorSelection.kind === "track") mapNetworkEditor.deleteTrack(editorSelection.id);
      else mapNetworkEditor.deleteNode(editorSelection.id);
    } else {
      const next = structuredClone(network);
      if (editorSelection.kind === "track") {
        next.track_sections = next.track_sections.filter(t => t.id !== editorSelection.id);
        next.crossovers = next.crossovers.filter(c => c.from_track_id !== editorSelection.id && c.to_track_id !== editorSelection.id);
        next.signal_blocks = next.signal_blocks.filter(b => b.track_section_id !== editorSelection.id);
        next.routes = next.routes.map(route => ({ ...route, track_section_ids: route.track_section_ids?.filter(id => id !== editorSelection.id) }));
      } else {
        const id = editorSelection.id;
        if (next.track_sections.some(t => t.start_node_id === id || t.end_node_id === id)) {
          setStatus("Нельзя удалить узел: сначала удалите связанные участки");
          return;
        }
        next.track_nodes = next.track_nodes.filter(n => n.id !== id);
      }
      network = next;
      markDirty();
    }
  } catch (error) {
    setStatus(error instanceof Error ? error.message : "Не удалось удалить объект");
    return;
  }
  editorSelection = { kind: null, id: null };
  renderPropertyPanel();
  render();
}
function setStatus(next: string): void {
  message = next;
  statusElement.textContent = next;
}
function markDirty(): void {
  projectRevision += 1;
  saveStateElement.textContent = "Изменения не сохранены";
  saveStateElement.classList.add("dirty");
  scheduleProjectSave();
}
function markClean(): void {
  saveStateElement.textContent = "Сохранено";
  saveStateElement.classList.remove("dirty");
}
function clampNumber(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, Number.isFinite(value) ? value : min));
}
function toLocalMeters(lon: number, lat: number, lon0: number, lat0: number) {
  const earthRadius = 6378137;
  const cosLat = Math.cos((lat0 * Math.PI) / 180);
  return {
    x: ((lon - lon0) * Math.PI) / 180 * earthRadius * cosLat,
    y: ((lat - lat0) * Math.PI) / 180 * earthRadius,
  };
}
function fromLocalMeters(x: number, y: number, lon0: number, lat0: number): [number, number] {
  const earthRadius = 6378137;
  const cosLat = Math.cos((lat0 * Math.PI) / 180);
  return [
    lon0 + (x / Math.max(1e-9, earthRadius * cosLat)) * 180 / Math.PI,
    lat0 + (y / earthRadius) * 180 / Math.PI,
  ];
}
function bounds(): Bounds | null {
  if (!map) return null;
  const b = map.getBounds();
  return { south: b.getSouth(), west: b.getWest(), north: b.getNorth(), east: b.getEast() };
}
function buildNetworkPayload(): NetworkPayload {
  const origin = stops[0] ?? { lon: INITIAL_MAP_CENTER[0], lat: INITIAL_MAP_CENTER[1] };
  const metricStops = stops.map((stop) => ({
    id: stop.id,
    name: stop.name,
    location: toLocalMeters(stop.lon, stop.lat, origin.lon, origin.lat),
    is_station: false,
  }));

  const coordinates = roadRoute?.features[0]?.geometry.coordinates;
  const geometry = coordinates && coordinates.length >= 2
    ? { points: coordinates.map(([lon, lat]) => toLocalMeters(lon, lat, origin.lon, origin.lat)) }
    : metricStops.length >= 2 ? { points: metricStops.map((stop) => stop.location) } : null;

  const vehicleType = {
    id: `vehicle-${mode}`,
    name: MODE_LABELS[mode],
    mode,
    capacity: MODE_CAPACITY[mode],
    operating_cost_per_km: 0,
  };

  const segmentPairs = stops.slice(0, -1).map((stop, index) => ({
    from: stop,
    to: stops[index + 1],
  }));

  const trackNodes = metricStops.map((stop) => ({ id: `node-${stop.id}`, x: stop.location.x, y: stop.location.y, elevation_m: 0 }));
  const trackSections = segmentPairs.map(({ from, to }, index) => {
    const distanceKm = Math.hypot(
      toLocalMeters(to.lon, to.lat, origin.lon, origin.lat).x -
        toLocalMeters(from.lon, from.lat, origin.lon, origin.lat).x,
      toLocalMeters(to.lon, to.lat, origin.lon, origin.lat).y -
        toLocalMeters(from.lon, from.lat, origin.lon, origin.lat).y,
    ) / 1000;

    return {
      id: `track-${index + 1}`,
      length_km: Math.max(0.001, distanceKm),
      track_type: (mode === "metro" ? "tunnel" : "surface") as TrackType,
      capacity_departures_per_hour: mode === "bus" ? 60 : 30,
      shared_group: null,
      station_ids: [from.id, to.id],
      speed_limit_kph: mode === "bus" ? 50 : mode === "tram" ? 50 : mode === "metro" ? 80 : 120,
      start_node_id: `node-${from.id}`,
      end_node_id: `node-${to.id}`,
      start_elevation_m: 0,
      end_elevation_m: 0,
      max_slope_percent: null,
      curve_radius_m: null,
      track_count: 1,
      direction: "both" as const,
      parallel_group: null,
      grade_crossing_count: 0,
    };
  });

  const trackSectionIds = trackSections.map((section) => section.id);

  return {
    origin_lon: origin.lon,
    origin_lat: origin.lat,
    stops: metricStops,
    routes: stops.length >= 2 ? [{
      id: "draft-route",
      name: routeName,
      mode,
      stop_ids: stops.map((stop) => stop.id),
      geometry,
      track_section_ids: trackSectionIds,
      both_ways: true,
      closed: false,
    }] : [],
    vehicle_types: [vehicleType],
    periods: PERIODS.map((period) => ({ ...period })),
    services: stops.length >= 2 ? [{
      id: "draft-service",
      route_id: "draft-route",
      vehicle_type_id: vehicleType.id,
      headway_by_period: { ...headways },
      departure_offset_by_period: Object.fromEntries(PERIODS.map((period) => [period.id, 0])),
      phase_by_period: Object.fromEntries(PERIODS.map((period) => [period.id, 0])),
    }] : [],
    track_nodes: trackNodes,
    track_sections: trackSections.map((section) => {
      const old = previousNetwork?.track_sections?.find((item) => item.id === section.id);
      return old ? { ...section, ...old } : section;
    }),
    crossovers: previousNetwork?.crossovers ?? [],
    signal_blocks: previousNetwork?.signal_blocks ?? [],
    stations: stops.map((stop) => ({
      id: `station-${stop.id}`,
      name: stop.name,
      stop_id: stop.id,
      platform_ids: [`platform-${stop.id}`],
      group_id: null,
      interchange: false,
      platform_length_m: mode === "bus" ? 30 : mode === "tram" ? 60 : 120,
    })),
    platforms: stops.map((stop) => ({
      id: `platform-${stop.id}`,
      station_id: `station-${stop.id}`,
      length_m: mode === "bus" ? 30 : mode === "tram" ? 60 : 120,
      track_ids: [],
      layout: "side" as const,
      number: 1,
    })),
    station_groups: [],
    rolling_stock: [{
      id: `stock-${mode}`,
      name: MODE_LABELS[mode],
      vehicle_type_id: vehicleType.id,
      car_capacity: Math.max(1, Math.round(MODE_CAPACITY[mode] / (mode === "bus" ? 1 : 100))),
      car_length_m: mode === "bus" ? 12 : 20,
      train_width_m: mode === "bus" ? 2.5 : 3,
      max_cars: mode === "bus" ? 1 : mode === "tram" ? 3 : mode === "metro" ? 8 : 10,
      max_speed_kph: mode === "bus" ? 60 : mode === "tram" ? 70 : mode === "metro" ? 90 : 120,
      acceleration_mps2: 1,
      deceleration_mps2: 1.2,
      lateral_acceleration_mps2: 1,
      minimum_curve_radius_m: mode === "bus" ? 30 : 120,
      dwell_seconds: 20,
      car_cost: 1,
      train_operating_cost_per_hour: 1,
      car_operating_cost_per_hour: 1,
      track_maintenance_cost_per_km_year: 1,
      station_maintenance_cost_per_year: 1,
      tph_limit: mode === "bus" ? 60 : 30,
    }],
    fare_groups: [{
      id: "default",
      name: "Основной тариф",
      fare_system: "flat",
      flat_fare: farePerTransitTrip,
      route_fares: {},
      transfer_policy: "none",
      transfer_window_min: 0,
      boarding_charge: 0,
      per_km_rate: 0,
      fare_cap: null,
      zones: [],
      zone_base_fare: 0,
      zone_per_zone_fare: 0,
    }],
  };
}
let previousNetwork: NetworkPayload | null = null;
let network = buildNetworkPayload();
previousNetwork = network;
let selectedTrackId: string | null = null;
let mapNetworkEditor: MapNetworkEditor | null = null;
let editorMode: MapEditorMode = "select";

function syncMapGeoJson(): void {
  if (!map || !mapReady) return;
  const source = (id: string) => map!.getSource(id) as GeoJSONSource | undefined;
  source("draft-route")?.setData(roadRoute ?? emptyRouteGeoJSON());
  source("draft-stops")?.setData(stopsGeoJSON());
  if (cityRoads) source("city-roads")?.setData(cityRoads);
  if (cityConnectors) source("city-connectors")?.setData(cityConnectors);
  if (cityStops) source("city-stops")?.setData(cityStops);
  if (cityPlaces) source("city-places")?.setData(cityPlaces);
  if (populationZones) source("population-zones")?.setData(populationZones);
  if (demandStreets) source("demand-streets")?.setData(demandStreets);
  source("analysis-sections")?.setData(assignmentSectionGeoJSON());
  source("analysis-stops")?.setData(assignmentStopGeoJSON());

  const setVisibility = (id: string, visible: boolean) => {
    if (map!.getLayer(id)) map!.setLayoutProperty(id, "visibility", visible ? "visible" : "none");
  };
  setVisibility("city-road-lines", showRoads);
  setVisibility("city-connector-circles", showConnectors);
  setVisibility("city-stop-circles", showStops);
  setVisibility("city-place-circles", showPlaces);
  setVisibility("population-zone-points", showPopulation);
  setVisibility("demand-street-lines", showDemandStreets);
  setVisibility("analysis-section-loads", showPassengerFlow);
  setVisibility("analysis-stop-loads", showStationLoads);
  if (map!.getLayer("city-road-lines")) {
    map!.setPaintProperty("city-road-lines", "line-color",
      showRoadSpeed
        ? ["interpolate", ["linear"], ["get", "speed_kph"], 10, "#ef4444", 30, "#eab308", 50, "#22c55e", 90, "#3b82f6"]
        : "#9ca3af");
  }
}

function stopsGeoJSON(): FeatureCollection<GeoJSONPoint, { id: string; name: string }> {
  return {
    type: "FeatureCollection",
    features: stops.map((stop) => ({
      type: "Feature",
      geometry: { type: "Point", coordinates: [stop.lon, stop.lat] },
      properties: { id: stop.id, name: stop.name },
    })),
  };
}
function emptyRouteGeoJSON(): FeatureCollection<LineString, object> {
  return { type: "FeatureCollection", features: [] };
}
function assignmentSectionGeoJSON(): FeatureCollection<LineString, Record<string, unknown>> {
  if (!assignmentResult) return { type: "FeatureCollection", features: [] };
  const byId = new Map(stops.map((stop) => [stop.id, stop]));
  return {
    type: "FeatureCollection",
    features: assignmentResult.section_loads.flatMap((section) => {
      const from = byId.get(section.from_stop_id);
      const to = byId.get(section.to_stop_id);
      return from && to ? [{
        type: "Feature",
        geometry: { type: "LineString", coordinates: [[from.lon, from.lat], [to.lon, to.lat]] },
        properties: section,
      }] : [];
    }),
  };
}
function assignmentStopGeoJSON(): FeatureCollection<GeoJSONPoint, Record<string, unknown>> {
  if (!assignmentResult) return { type: "FeatureCollection", features: [] };
  const byId = new Map(stops.map((stop) => [stop.id, stop]));
  return {
    type: "FeatureCollection",
    features: assignmentResult.stop_flows.flatMap((flow) => {
      const stop = byId.get(flow.stop_id);
      return stop ? [{
        type: "Feature",
        geometry: { type: "Point", coordinates: [stop.lon, stop.lat] },
        properties: flow,
      }] : [];
    }),
  };
}
function datasetCacheKey(prefix: string, b: Bounds): string {
  const round = (value: number) => value.toFixed(4);
  return `${prefix}:${round(b.south)}:${round(b.west)}:${round(b.north)}:${round(b.east)}`;
}


function previewZones() {
  const origin = stops[0];
  if (!origin) return [];
  return stops.map((stop) => {
    const point = toLocalMeters(stop.lon, stop.lat, origin.lon, origin.lat);
    return { id: stop.id, centroid_x: point.x, centroid_y: point.y };
  });
}

async function loadCityData(): Promise<void> {
  if (!map) return;
  busy = true;
  setStatus("Проверка локального кэша Overture…");
  try {
    const b = bounds();
    if (!b) throw new Error("Карта ещё не готова");
    const key = datasetCacheKey("overture-network", b);
    const metaKey = key + ":meta";
    const roadsKey = key + ":roads-bin";
    const cachedMeta = await loadDataset<Omit<OvertureNetworkResponse, "roads"> & {
      roadProperties: Array<Record<string, unknown> | null>;
    }>(metaKey);
    const cachedRoads = await loadBinaryDataset(roadsKey);

    let data: OvertureNetworkResponse;
    if (cachedMeta && cachedRoads) {
      const decoded = decodeLines(cachedRoads);
      data = {
        roads: {
          type: "FeatureCollection",
          features: decoded.lines.map((line, index) => ({
            type: "Feature",
            geometry: { type: "LineString", coordinates: line.coordinates },
            properties: cachedMeta.roadProperties[index] ?? {},
          })),
        },
        connectors: cachedMeta.connectors,
        stops: cachedMeta.stops,
        places: cachedMeta.places,
        release: cachedMeta.release,
        counts: cachedMeta.counts,
      };
    } else {
      data = await loadOvertureNetwork(b.south, b.west, b.north, b.east);
      await Promise.all([
        saveDataset(metaKey, {
          connectors: data.connectors,
          stops: data.stops,
          places: data.places,
          release: data.release,
          counts: data.counts,
          roadProperties: data.roads.features.map((feature) => feature.properties ?? {}),
        }),
        saveBinaryDataset(
          roadsKey,
          encodeLines(data.roads.features.map((feature) => ({
            coordinates: feature.geometry.coordinates.map(([lon, lat]) => [lon, lat] as [number, number]),
          }))),
        ),
      ]);
    }
    cityRoads = data.roads;
    cityConnectors = data.connectors;
    cityStops = data.stops;
    cityPlaces = data.places;

    const graphKey = key + ":graph-bin";
    const cachedGraph = await loadBinaryDataset(graphKey);
    try {
      const graphBuffer = cachedGraph ?? (await loadOvertureGraph(b.south, b.west, b.north, b.east));
      if (!cachedGraph) await saveBinaryDataset(graphKey, graphBuffer);
      streetGraph = decodeStreets(graphBuffer);
    } catch (error) {
      streetGraph = null;
      setStatus(
        `${error instanceof Error ? error.message : "Street graph недоступен"}`,
      );
    }

      const populationKey = datasetCacheKey("population-zones", b);
    const cachedPopulation = await loadDataset<FeatureCollection>(populationKey);
    if (cachedPopulation) {
      populationZones = cachedPopulation;
    } else {
      populationZones = await loadPopulationZones(b.south, b.west, b.north, b.east);
      await saveDataset(populationKey, populationZones);
    }
    setStatus(`${cachedMeta && cachedRoads ? "Кэш Overture" : "Overture"} ${data.release}: ${data.counts.roads} участков, ${data.counts.connectors} коннекторов, ${data.counts.stops} остановок`);
    syncMapGeoJson();
  } catch (error) {
    setStatus(error instanceof Error ? error.message : "Ошибка загрузки Overture");
  } finally {
    busy = false;
    render();
  }
}

async function buildRoadRoute(): Promise<void> {
  if (!map || stops.length < 2) return;
  busy = true;
  setStatus("Построение маршрута по street graph...");
  try {
    if (!streetGraph) {
      throw new Error("Street graph не загружен: обновите область карты (Данные Overture)");
    }
    const graph = streetGraph;
    const snapped: number[] = [];
    for (const stop of stops) {
      const node = nearestGraphNode(graph, stop.lon, stop.lat);
      if (node === null) {
        throw new Error(
          `Точка ${stop.lon.toFixed(5)},${stop.lat.toFixed(5)} дальше 150 м от уличного графа`,
        );
      }
      snapped.push(node);
    }

    const requests: Array<{ start: number; end: number }> = [];
    for (let index = 0; index + 1 < snapped.length; index += 1) {
      requests.push({ start: snapped[index], end: snapped[index + 1] });
    }
    const legs = await routeStreets(graph, requests);

    const coordinates: Array<[number, number]> = [];
    let travelTimeMin = 0;
    let lengthM = 0;
    for (const leg of legs) {
      for (const point of assembleRouteCoordinates(graph, leg.nodes, leg.edges)) {
        const last = coordinates[coordinates.length - 1];
        if (last && last[0] === point[0] && last[1] === point[1]) continue;
        coordinates.push(point);
      }
      travelTimeMin += leg.costMin;
      for (const edge of leg.edges) lengthM += graph.edgeLengthM[edge];
    }
    if (coordinates.length < 2) throw new Error("Маршрут по уличному графу пуст");

    roadRoute = {
      type: "FeatureCollection",
      features: [{
        type: "Feature",
        geometry: { type: "LineString", coordinates },
        properties: {
          travel_time_min: travelTimeMin,
          length_m: lengthM,
          source: "street-graph-worker",
        },
      }],
    };
    previousNetwork = network;
    network = buildNetworkPayload();
    setStatus(`Street graph маршрут: ${(lengthM / 1000).toFixed(2)} км, ${travelTimeMin.toFixed(1)} мин`);
    syncMapGeoJson();
    markDirty();
  } catch (error) {
    roadRoute = null;
    setStatus(error instanceof Error ? error.message : "Ошибка построения маршрута");
  } finally {
    busy = false;
    render();
  }
}

function runPlanningPreview(): void {
  network = keepNetwork(network, buildNetworkPayload());
  lastPlanningPreview = planningPreview(network);
  lastProbeDelta = scenarioBase ? probe(scenarioBase.network, network).delta : null;
  setStatus(
    "Быстрый расчёт: " +
    lastPlanningPreview.routeLengthKm.toFixed(2) +
    " км · " +
    lastPlanningPreview.dailyDepartures +
    " отправлений · парк " +
    lastPlanningPreview.fleetEstimate,
  );
  renderResults();
  render();
}

async function runPreview(): Promise<void> {
  if (stops.length < 2) return;
  busy = true;
  setStatus("Расчёт проверочного пассажиропотока…");
  try {
    const previousSegmentState = previousSegmentSignatures;
    network = keepNetwork(network, buildNetworkPayload());
    const changed = changedSegments(previousSegmentState, network);
    previousSegmentSignatures = segmentSignatures(network);
    lastPlanningPreview = planningPreview(network);
    evaluationSummary = networkCounts(network);
    if (!evaluationClient) evaluationClient = createEvaluationClient();
    const b = bounds();
    if (!b) throw new Error("Карта ещё не готова");
    const demandInput = await loadReferenceDemand(
      b.south, b.west, b.north, b.east, network.origin_lon, network.origin_lat,
    );
    await runRuntimePreview(evaluationClient, network, demandInput);
    const demand = [{
      origin_zone_id: stops[0].id,
      destination_zone_id: stops[stops.length - 1].id,
      trips_per_day: previewTrips,
      purpose: "all",
    }];
    assignmentResult = await calculateAssignment(network, demand, previewZones(), "am");
    demandStreets = await loadDemandStreets(demand, previewZones(), stops[0].lon, stops[0].lat);
    setStatus(`Пассажиропоток рассчитан: transit ${(assignmentResult.metrics.transit_share * 100).toFixed(1)}% · изменено сегментов ${changed.length}`);
    renderResults();
    syncMapGeoJson();
  } catch (error) {
    setStatus(error instanceof Error ? error.message : "Ошибка расчёта пассажиропотока");
  } finally {
    busy = false;
    render();
  }
}

async function runCityAssignment(): Promise<void> {
  if (!map || stops.length < 2) return;
  busy = true;
  setStatus("Расчёт городской сети по WorldPop + Overture…");
  try {
    network = buildNetworkPayload();
    const b = bounds();
    if (!b) throw new Error("Карта ещё не готова");
    const result = await calculateCityAssignment(
      network, b.south, b.west, b.north, b.east,
      network.origin_lon, network.origin_lat,
      "am", farePerTransitTrip, annualDays,
    );
    assignmentResult = result.assignment;
    cityAssignmentMeta = result.data;
    cityAssignmentPeriods = result.periods;
    economicsResult = { scenario_id: "citywide", name: "Городская сеть", economics: result.economics };
    setStatus(`Городской расчёт: ${result.data.od_pairs} OD-пар`);
    renderResults();
    syncMapGeoJson();
  } catch (error) {
    setStatus(error instanceof Error ? error.message : "Ошибка городского расчёта");
  } finally {
    busy = false;
    render();
  }
}

async function runEconomics(): Promise<void> {
  if (stops.length < 2) return;
  busy = true;
  setStatus("Расчёт экономики…");
  try {
    network = buildNetworkPayload();
    economicsResult = await calculateEconomics(
      network,
      [{
        origin_zone_id: stops[0].id,
        destination_zone_id: stops[stops.length - 1].id,
        trips_per_day: previewTrips,
        purpose: "all",
      }],
      previewZones(),
      "am",
      farePerTransitTrip,
      annualDays,
    );
    setStatus("Экономика рассчитана");
    renderResults();
  } catch (error) {
    setStatus(error instanceof Error ? error.message : "Ошибка расчёта экономики");
  } finally {
    busy = false;
    render();
  }
}

function scenarioPayload(id: string, name: string, scenarioNetwork: NetworkPayload, origin: StopDraft, destination: StopDraft, trips: number, fare: number, days: number): ScenarioPayload {
  const lon0 = scenarioNetwork.origin_lon;
  const lat0 = scenarioNetwork.origin_lat;
  const zone = (stop: StopDraft, zoneId: string) => {
    const point = toLocalMeters(stop.lon, stop.lat, lon0, lat0);
    return { id: zoneId, centroid_x: point.x, centroid_y: point.y };
  };
  return {
    id, name, network: scenarioNetwork,
    demand: [{ origin_zone_id: "scenario-origin", destination_zone_id: "scenario-destination", trips_per_day: trips, purpose: "all" }],
    zones: [zone(origin, "scenario-origin"), zone(destination, "scenario-destination")],
    config: { period_id: "am" },
    economics_config: { period_id: "am", fare_per_transit_trip: fare, annual_days: days },
  };
}
function captureScenarioBase(): void {
  if (stops.length < 2) return;
  scenarioBase = {
    network: structuredClone(network),
    origin: { lon: stops[0].lon, lat: stops[0].lat },
    destination: { lon: stops[stops.length - 1].lon, lat: stops[stops.length - 1].lat },
    trips: previewTrips,
    farePerTransitTrip,
    annualDays,
  };
  scenarioComparison = null;
  markDirty();
  setStatus("Базовый сценарий зафиксирован");
  render();
}
async function compareWithBase(): Promise<void> {
  if (!scenarioBase || stops.length < 2) return;
  busy = true;
  setStatus("Сравнение базового и текущего сценариев…");
  try {
    const origin: StopDraft = { id: "origin", name: "Источник", ...scenarioBase.origin };
    const destination: StopDraft = { id: "destination", name: "Назначение", ...scenarioBase.destination };
    scenarioComparison = await compareScenarios(
      scenarioPayload("base", "Базовый сценарий", scenarioBase.network, origin, destination, scenarioBase.trips, scenarioBase.farePerTransitTrip, scenarioBase.annualDays),
      scenarioPayload("alternative", "Текущий сценарий", network, origin, destination, scenarioBase.trips, farePerTransitTrip, annualDays),
    );
    setStatus("Сравнение сценариев завершено");
    renderResults();
  } catch (error) {
    setStatus(error instanceof Error ? error.message : "Ошибка сравнения сценариев");
  } finally {
    busy = false;
    render();
  }
}
async function generateTimetable(): Promise<void> {
  const service = network.services[0];
  if (!service) return;
  busy = true;
  try {
    timetable = await createTimetable(service.id, network.periods, service.headway_by_period);
    setStatus("Расписание сформировано");
    renderResults();
  } catch (error) {
    setStatus(error instanceof Error ? error.message : "Ошибка формирования расписания");
  } finally {
    busy = false;
    render();
  }
}
function projectData(): ProjectFile {
  return {
    format: "transit-planner-project",
    version: 4,
    routeName,
    mode,
    headways,
    stops,
    previewTrips,
    network,
    roadRoute,
    economics: { farePerTransitTrip, annualDays },
    scenarioBase,
  };
}
function scheduleProjectSave(): void {
  if (!initialized) return;
  if (autosaveTimer) clearTimeout(autosaveTimer);
  const revision = projectRevision;
  autosaveTimer = setTimeout(() => {
    autosaveTimer = null;
    void saveProject("current", projectData()).then(() => {
      if (revision === projectRevision) markClean();
    }).catch(() => {
      if (revision === projectRevision) setStatus("Изменения остаются только в текущем сеансе");
    });
  }, 350);
}
function exportJson(): void {
  const blob = new Blob([JSON.stringify(projectData(), null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "transit-network.json";
  anchor.click();
  URL.revokeObjectURL(url);
  projectRevision += 1;
  markClean();
  void saveProject("current", projectData());
  setStatus("JSON сети экспортирован");
}
function applyProject(project: ProjectFile): void {
  if (project.format !== "transit-planner-project") throw new Error("Неверный формат проекта");
  const version = Number(project.version ?? 1);
  if (version < 1 || version > 4) throw new Error("Неподдерживаемая версия проекта");
  routeName = String(project.routeName ?? "Новый маршрут");
  mode = project.mode ?? "bus";
  headways = { ...headways, ...(project.headways ?? {}) };
  stops = Array.isArray(project.stops) ? project.stops : [];
  previewTrips = clampNumber(Number(project.previewTrips), 1, 100000);
  roadRoute = version >= 3 && project.roadRoute?.type === "FeatureCollection" ? project.roadRoute : null;
  const economics = project.economics ?? {};
  farePerTransitTrip = clampNumber(Number(economics.farePerTransitTrip), 0, Number.MAX_SAFE_INTEGER);
  annualDays = Math.round(clampNumber(Number(economics.annualDays), 1, 366));
  scenarioBase = version >= 2 && project.scenarioBase?.network ? project.scenarioBase : null;
  assignmentResult = null;
  economicsResult = null;
  scenarioComparison = null;
  timetable = null;
  network = buildNetworkPayload();
  projectRevision += 1;
  markClean();
  render();
  syncMapGeoJson();
  setStatus("Проект загружен");
}
function addStop(event: MapMouseEvent): void {
  if (!drawMode) return;
  const index = stops.length + 1;
  stops = [...stops, { id: `stop-${Date.now()}-${index}`, name: `Остановка ${index}`, lon: event.lngLat.lng, lat: event.lngLat.lat }];
  roadRoute = null;
  assignmentResult = null;
  demandStreets = null;
  scenarioComparison = null;
  network = buildNetworkPayload();
  markDirty();
  render();
  syncMapGeoJson();
}
function removeStop(id: string): void {
  stops = stops.filter((stop) => stop.id !== id);
  roadRoute = null;
  assignmentResult = null;
  demandStreets = null;
  scenarioComparison = null;
  network = buildNetworkPayload();
  markDirty();
  render();
  syncMapGeoJson();
}
function clearRoute(): void {
  stops = [];
  roadRoute = null;
  assignmentResult = null;
  demandStreets = null;
  economicsResult = null;
  scenarioComparison = null;
  timetable = null;
  network = buildNetworkPayload();
  markDirty();
  render();
  syncMapGeoJson();
  setStatus("Маршрут очищен");
}
function formatNumber(value: unknown): string {
  return typeof value === "number" ? value.toLocaleString("ru-RU", { maximumFractionDigits: 1 }) : String(value);
}


function renderPhysicalEditor(): string {
  const selected = network.track_sections.find((item) => item.id === selectedTrackId) ?? network.track_sections[0];
  if (!selected) {
    return `<section class="analytics-panel"><h3>Физическая инфраструктура</h3><p>Добавьте минимум две остановки, чтобы создать участок сети.</p></section>`;
  }
  selectedTrackId = selected.id;
  const field = (label: string, key: string, value: unknown, type = "number") =>
    `<label>${label}<input data-track-field="${key}" type="${type}" value="${escapeHtml(String(value ?? ""))}" /></label>`;
  return `<section class="analytics-panel physical-editor">
    <h3>Физическая инфраструктура</h3>
    <div class="network-table-wrap"><table class="network-table"><thead><tr><th>Участок</th><th>Тип</th><th>Длина, км</th><th>Путь</th><th>Направление</th></tr></thead><tbody>
      ${network.track_sections.map((track) => `<tr data-track-select="${track.id}" class="${track.id === selected.id ? "selected" : ""}"><td>${track.id}</td><td>${track.track_type}</td><td>${track.length_km.toFixed(3)}</td><td>${track.track_count ?? 1}</td><td>${track.direction ?? "both"}</td></tr>`).join("")}
    </tbody></table></div>
    <div class="physical-form">
      <label>Тип<select data-track-field="track_type">${(["surface","elevated","tunnel","trenched","ramp"] as const).map((v) => `<option value="${v}" ${selected.track_type === v ? "selected" : ""}>${v}</option>`).join("")}</select></label>
      <label>Направление<select data-track-field="direction">${(["forward","reverse","both"] as const).map((v) => `<option value="${v}" ${selected.direction === v ? "selected" : ""}>${v}</option>`).join("")}</select></label>
      ${field("Скорость, км/ч","speed_limit_kph",selected.speed_limit_kph ?? "")}
      ${field("Количество путей","track_count",selected.track_count ?? 1)}
      ${field("Радиус кривой, м","curve_radius_m",selected.curve_radius_m ?? "")}
      ${field("Макс. уклон, %","max_slope_percent",selected.max_slope_percent ?? "")}
      ${field("Высота начала, м","start_elevation_m",selected.start_elevation_m ?? 0)}
      ${field("Высота конца, м","end_elevation_m",selected.end_elevation_m ?? 0)}
      ${field("Переезды","grade_crossing_count",selected.grade_crossing_count ?? 0)}
      <label>Параллельная группа<input data-track-field="parallel_group" type="text" value="${escapeHtml(selected.parallel_group ?? "")}" /></label>
    </div>
    <div class="network-kpis"><div><span>Перепад высоты</span><b>${((selected.end_elevation_m ?? 0) - (selected.start_elevation_m ?? 0)).toFixed(1)} м</b></div></div>
  </section>`;
}
function renderResults(): void {
  const metricGrid = (items: Array<[string, unknown]>) =>
    `<div class="analytics-grid">${items.map(([name, value]) => `<div><span>${name}</span><b>${formatNumber(value)}</b></div>`).join("")}</div>`;
  let html = `
    <div class="network-header">
      <div><h2>${viewMode === "network" ? "Сеть" : "Расчёт"}</h2><p>Линии, интервалы, городские данные и аналитика</p></div>
      <div class="network-kpis">
        <div><span>Линий</span><b>${network.routes.length}</b></div>
        <div><span>Отправлений/сутки</span><b>${evaluationSummary.dailyDepartures}</b></div>
        <div><span>Остановок</span><b>${network.stops.length}</b></div>
      </div>
    </div>
    <section class="analytics-panel">
      <h3>Сеть</h3>
      <div class="network-table-wrap"><table class="network-table"><thead><tr><th>Линия</th><th>Вид транспорта</th><th>Остановки</th><th>Вместимость</th>${PERIODS.map((p) => `<th>${p.id}</th>`).join("")}</tr></thead><tbody>
      ${network.routes.map((route) => {
        const service = network.services.find((item) => item.route_id === route.id);
        const vehicle = network.vehicle_types.find((item) => item.id === service?.vehicle_type_id);
        return `<tr><td><strong>${route.name}</strong></td><td>${MODE_LABELS[route.mode]}</td><td>${route.stop_ids.length}</td><td>${vehicle?.capacity ?? "—"}</td>${PERIODS.map((p) => `<td>${service?.headway_by_period[p.id] ?? "—"}</td>`).join("")}</tr>`;
      }).join("")}</tbody></table></div>
    </section>
  `;
  html += renderPhysicalEditor();  if (network.services.length) {
    const service = network.services[0];
    const route = network.routes.find(r => r.id === service.route_id);
    const lengthKm = route?.track_section_ids?.reduce((sum, id) => sum + (network.track_sections.find(t => t.id === id)?.length_km ?? 0), 0) ?? 0;
    const speed = Math.max(5, network.track_sections.filter(t => route?.track_section_ids?.includes(t.id)).reduce((sum,t)=>sum+(t.speed_limit_kph ?? 30),0) / Math.max(1, route?.track_section_ids?.length ?? 1));
    const cycleMinutes = Math.max(10, lengthKm / speed * 60 * 2 + 10);
    const fleet = estimateFleetRequirement(network, service.id, cycleMinutes);
    html += `<section class="analytics-panel"><h3>Эксплуатация</h3>${metricGrid([
      ["Длина линии, км", lengthKm],
      ["Расчётный оборот, мин", cycleMinutes],
      ["Парк AM", fleet.find(x=>x.period_id==="am")?.vehicles_required ?? 0],
      ["Парк PM", fleet.find(x=>x.period_id==="pm")?.vehicles_required ?? 0],
    ])}<div class="timetable-panel">${generateServiceTimetable(network, service.id).map(p => `<div class="timetable-row"><strong>${p.period_id}</strong><span>${p.departures_minute.length} отправлений</span></div>`).join("")}</div></section>`;
  }

  if (cityAssignmentMeta) {
    html += `<section class="analytics-panel"><h3>Городской расчёт</h3>${metricGrid([
      ["Зоны", cityAssignmentMeta.zones],
      ["Places", cityAssignmentMeta.places],
      ["OD-пары", cityAssignmentMeta.od_pairs],
      ["Спрос/сутки", cityAssignmentMeta.total_demand_trips],
    ])}</section>`;
  }
  if (assignmentResult) {
    html += `<section class="analytics-panel"><h3>Пассажиропоток</h3>${metricGrid([
      ["Общий спрос", assignmentResult.metrics.total_trips],
      ["Общественный транспорт", assignmentResult.metrics.transit_trips],
      ["Автомобиль", assignmentResult.metrics.car_trips],
      ["Пешком / велосипед", assignmentResult.metrics.walk_trips + assignmentResult.metrics.bike_trips],
      ["Среднее время", assignmentResult.metrics.average_transit_time_min],
      ["Пересадки", assignmentResult.metrics.average_transfers],
      ["Макс. загрузка", assignmentResult.max_load_ratio * 100],
      ["Неназначенный ОТ", assignmentResult.unserved_transit_demand],
      ["Транзитная доля", assignmentResult.metrics.transit_share * 100],
    ])}</section>`;
  }
  if (cityAssignmentPeriods.length) {
    html += `<section class="analytics-panel"><h3>Линия × период</h3>${cityAssignmentPeriods.map((period) => `
      <div class="period-card"><strong>${period.period_id}</strong><span>спрос ${formatNumber(period.demand_trips)}</span><span>общественный транспорт ${(period.transit_share * 100).toFixed(1)}%</span><span>макс. загрузка ${(period.max_load_ratio * 100).toFixed(1)}%</span><span>эксплуатация ${formatNumber(period.economics.daily_operating_cost)}</span>${period.services.map((service) => `<span>${service.route_id}: ${formatNumber(service.riders)} пасс. · PLF ${(service.peak_load_factor * 100).toFixed(1)}% · парк ${service.fleet}</span>`).join("")}</div>`).join("")}</section>`;
  }
  if (economicsResult) {
    const e = economicsResult.economics;
    html += `<section class="analytics-panel economics-panel"><h3>Экономика</h3>${metricGrid([
      ["Транспортная работа", e.daily_vehicle_km],
      ["Эксплуатация", e.daily_operating_cost],
      ["Стоимость парка", e.daily_fleet_cost],
      ["Выручка", e.daily_fare_revenue],
      ["OPEX на поездку", e.operating_cost_per_transit_trip],
    ])}</section>`;
  }
  if (scenarioComparison) {
    html += `<section class="analytics-panel"><h3>Сравнение сценариев</h3><p>Участков: ${scenarioComparison.comparison.sections.length} · линий-периодов: ${scenarioComparison.comparison.services.length}</p></section>`;
  }
  if (timetable) {
    html += `<section class="analytics-panel"><h3>Расписание ${timetable.service_id}</h3>${timetable.periods.map((period) => `<div class="timetable-row"><strong>${period.period_id}</strong><span>${period.departures_minute.length} отправлений</span></div>`).join("")}</section>`;
  }
  html += `<section class="analytics-panel"><button data-action="timetable" ${network.services.length ? "" : "disabled"}>Сформировать расписание</button></section>`;
  networkContent.innerHTML = html;
}
function renderStops(): void {
  stopCountElement.textContent = `(${stops.length})`;
  stopListElement.innerHTML = stops.length
    ? `${stops.map((stop, index) => `<div class="stop-row"><div class="stop-number">${index + 1}</div><div class="stop-copy"><strong>${escapeHtml(stop.name)}</strong><small>${stop.lon.toFixed(5)}, ${stop.lat.toFixed(5)}</small></div><button class="icon-button" data-remove-stop="${escapeHtml(stop.id)}" aria-label="Удалить остановку">×</button></div>`).join("")}<button data-action="clear">Очистить маршрут</button>`
    : `<div class="empty">Включите «Добавить остановки» и кликайте по карте.</div>`;
}
function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char] ?? char);
}
function render(): void {
  shell.setAttribute("aria-busy", String(busy));
  network = buildNetworkPayload();
  routeNameInput.value = routeName;
  modeInput.value = mode;
  previewTripsInput.value = String(previewTrips);
  fareInput.value = String(farePerTransitTrip);
  annualDaysInput.value = String(annualDays);
  headwayContainer.innerHTML = PERIODS.map((period) =>
    `<label>${period.id}<input data-headway="${period.id}" type="number" min="1" max="120" step="1" value="${headways[period.id] ?? 20}" /></label>`
  ).join("");
  renderStops();
  renderResults();
  const canRoute = stops.length >= 2;
  for (const selector of ['[data-action="build-road"]','[data-action="validate"]','[data-action="capture-base"]','[data-action="preview"]','[data-action="economics"]','[data-action="city-assignment"]']) {
    const button = shell.querySelector<HTMLButtonElement>(selector);
    if (button) button.disabled = busy || !canRoute;
  }
  const compareButton = shell.querySelector<HTMLButtonElement>('[data-action="compare"]');
  if (compareButton) compareButton.disabled = busy || !scenarioBase || !canRoute;
  const drawButton = shell.querySelector<HTMLButtonElement>('[data-action="draw"]');
  if (drawButton) {
    drawButton.textContent = drawMode ? "Завершить рисование" : "Добавить остановки";
    drawButton.setAttribute("aria-pressed", String(drawMode));
    drawButton.classList.toggle("active", drawMode);
  }
  mapHint.hidden = !drawMode || viewMode !== "map";
  mapArea.hidden = viewMode !== "map";
  networkArea.hidden = viewMode !== "network";
  shell.querySelectorAll<HTMLButtonElement>('[data-action="view-map"],[data-action="view-network"]').forEach((button) => {
    button.classList.toggle("active-toggle", (button.dataset.action === "view-map" && viewMode === "map") || (button.dataset.action === "view-network" && viewMode === "network"));
  });
  for (const [element] of toggleInputs) element.dispatchEvent(new Event("sync"));
  syncMapGeoJson();
}

shell.addEventListener("change", (event) => {
  const target = event.target as HTMLInputElement | HTMLSelectElement;
  const field = target.dataset.trackField;
  if (!field) return;
  const track = network.track_sections.find((item) => item.id === selectedTrackId);
  if (!track) return;
  if (field === "track_type" || field === "direction") {
    (track as any)[field] = target.value;
  } else if (field === "parallel_group") {
    track.parallel_group = target.value || null;
  } else {
    const value = Number(target.value);
    (track as any)[field] = Number.isFinite(value) ? value : null;
  }
  markDirty();
  render();
});

shell.addEventListener("click", (event) => {
  const target = event.target as HTMLElement;
  const action = target.closest<HTMLElement>("[data-action]")?.dataset.action;
  if (target.closest("[data-remove-stop]")) {
    removeStop((target.closest("[data-remove-stop]") as HTMLElement).dataset.removeStop!);
    return;
  }
  const selectedTrack = target.closest<HTMLElement>("[data-track-select]")?.dataset.trackSelect;
  if (selectedTrack) { selectedTrackId = selectedTrack; render(); return; }
  switch (action) {
    case "split-track":
      try { mapNetworkEditor?.splitSelectedTrack(); renderPropertyPanel(); render(); setStatus("Участок разделён"); }
      catch (error) { setStatus(error instanceof Error ? error.message : "Не удалось разделить участок"); }
      break;
    case "merge-track":
      try { mapNetworkEditor?.mergeSelectedTracks(); renderPropertyPanel(); render(); setStatus("Выберите второй участок и повторите «Объединить»"); }
      catch (error) { setStatus(error instanceof Error ? error.message : "Не удалось объединить участки"); }
      break;
    case "view-map": viewMode = "map"; render(); break;
    case "view-network": viewMode = "network"; render(); break;
    case "load-city": void loadCityData(); break;
    case "build-road": void buildRoadRoute(); break;
    case "draw": drawMode = !drawMode; render(); break;
    case "planning-preview": runPlanningPreview(); break;
    case "preview": void runPreview(); break;
    case "economics": void runEconomics(); break;
    case "city-assignment": void runCityAssignment(); break;
    case "capture-base": captureScenarioBase(); break;
    case "compare": void compareWithBase(); break;
    case "build-route-from-network":
      try {
        const result = new RouteEditor(network).buildRoute("draft-route", routeName, mode, network.track_sections.map(t => t.id), stops.map(s => s.id));
        network = result.network; previousNetwork = structuredClone(network); markDirty(); mapNetworkEditor?.refresh(); setStatus("Маршрут собран из участков сети"); render();
      } catch (error) { setStatus(error instanceof Error ? error.message : "Не удалось собрать маршрут"); }
      break;
    case "timetable": void generateTimetable(); break;
    case "validate":
      busy = true;
      void validateNetwork(network).then((result) => { const topology = validateTopology(network); const errors = [...result.errors, ...topology.filter(issue => issue.severity === "error").map(issue => issue.message)]; const warnings = topology.filter(issue => issue.severity === "warning").map(issue => issue.message); setStatus(errors.length ? `Ошибки: ${errors.join("; ")}` : warnings.length ? `Сеть корректна · предупреждения: ${warnings.join("; ")}` : "Сеть корректна"); })
        .catch((error) => setStatus(error instanceof Error ? error.message : "Ошибка проверки"))
        .finally(() => { busy = false; render(); });
      break;
    case "save": exportJson(); break;
    case "open": fileInput.click(); break;
    case "clear": clearRoute(); break;
  }
});

routeNameInput.addEventListener("input", () => {
  routeName = routeNameInput.value;
  markDirty();
});
modeInput.addEventListener("change", () => {
  mode = modeInput.value as TransitMode;
  markDirty();
  render();
});
previewTripsInput.addEventListener("change", () => {
  previewTrips = Math.round(clampNumber(Number(previewTripsInput.value), 1, 100000));
  previewTripsInput.value = String(previewTrips);
  markDirty();
});
fareInput.addEventListener("change", () => {
  farePerTransitTrip = clampNumber(Number(fareInput.value), 0, Number.MAX_SAFE_INTEGER);
  fareInput.value = String(farePerTransitTrip);
  markDirty();
});
annualDaysInput.addEventListener("change", () => {
  annualDays = Math.round(clampNumber(Number(annualDaysInput.value), 1, 366));
  annualDaysInput.value = String(annualDays);
  markDirty();
});
headwayContainer.addEventListener("change", (event) => {
  const input = (event.target as HTMLInputElement).closest<HTMLInputElement>("[data-headway]");
  if (!input) return;
  headways[input.dataset.headway!] = clampNumber(Number(input.value), 1, 120);
  markDirty();
});
for (const [element, key] of toggleInputs) {
  element.addEventListener("change", () => {
    initialSettings[key] = element.checked;
    switch (key) {
      case "showRoads": showRoads = element.checked; break;
      case "showRoadSpeed": showRoadSpeed = element.checked; break;
      case "showStops": showStops = element.checked; break;
      case "showPlaces": showPlaces = element.checked; break;
      case "showConnectors": showConnectors = element.checked; break;
      case "showPopulation": showPopulation = element.checked; break;
      case "showDemandStreets": showDemandStreets = element.checked; break;
      case "showPassengerFlow": showPassengerFlow = element.checked; break;
      case "showStationLoads": showStationLoads = element.checked; break;
    }
    saveUiSettings({ ...initialSettings });
    syncMapGeoJson();
  });
  element.addEventListener("sync", () => {
    switch (key) {
      case "showRoads": element.checked = showRoads; break;
      case "showRoadSpeed": element.checked = showRoadSpeed; break;
      case "showStops": element.checked = showStops; break;
      case "showPlaces": element.checked = showPlaces; break;
      case "showConnectors": element.checked = showConnectors; break;
      case "showPopulation": element.checked = showPopulation; break;
      case "showDemandStreets": element.checked = showDemandStreets; break;
      case "showPassengerFlow": element.checked = showPassengerFlow; break;
      case "showStationLoads": element.checked = showStationLoads; break;
    }
  });
}
window.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z") { event.preventDefault(); if (event.shiftKey) mapNetworkEditor?.redo(); else mapNetworkEditor?.undo(); renderPropertyPanel(); render(); return; }
  if (event.key !== "Escape" || !busy) return;
  evaluationClient?.cancel();
  busy = false;
  setStatus("Расчёт отменён");
  render();
});

fileInput.addEventListener("change", () => {
  const file = fileInput.files?.[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    try { applyProject(JSON.parse(String(reader.result)) as ProjectFile); }
    catch (error) { setStatus(error instanceof Error ? error.message : "Не удалось загрузить проект"); }
  };
  reader.readAsText(file);
  fileInput.value = "";
});

function initializeMap(): void {
  map = new MapLibreMap({ container: mapElement, style: MAP_STYLE, center: INITIAL_MAP_CENTER, zoom: 11 });
  map.addControl(new NavigationControl(), "top-right");
  map.on("load", () => {
    const blank = { type: "FeatureCollection", features: [] } as FeatureCollection;
    map!.addSource("city-roads", { type: "geojson", data: blank });
    map!.addLayer({ id: "city-road-lines", type: "line", source: "city-roads", paint: { "line-color": "#9ca3af", "line-width": 1.2, "line-opacity": 0.65 } });
    map!.addSource("city-connectors", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    map!.addLayer({ id: "city-connector-circles", type: "circle", source: "city-connectors", paint: { "circle-radius": 2.5, "circle-color": "#f59e0b", "circle-opacity": 0.7 } });
    map!.addSource("city-places", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    map!.addLayer({ id: "city-place-circles", type: "circle", source: "city-places", paint: { "circle-radius": 3, "circle-color": "#8b5cf6", "circle-opacity": 0.5 } });
    map!.addSource("city-stops", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    map!.addLayer({ id: "city-stop-circles", type: "circle", source: "city-stops", paint: { "circle-radius": 3.5, "circle-color": "#6b7280", "circle-opacity": 0.65, "circle-stroke-width": 1, "circle-stroke-color": "#fff" } });
    map!.addSource("population-zones", { type: "geojson", data: blank });
    map!.addLayer({ id: "population-zone-points", type: "circle", source: "population-zones", paint: { "circle-radius": ["interpolate", ["linear"], ["get", "population"], 0, 2, 500, 5, 2000, 9, 5000, 15], "circle-opacity": 0.28, "circle-color": "#0f766e" } });
    map!.addSource("demand-streets", { type: "geojson", data: blank });
    map!.addLayer({ id: "demand-street-lines", type: "line", source: "demand-streets", paint: { "line-width": ["interpolate", ["linear"], ["get", "flow_weight"], 0, 1, 100, 3, 500, 7, 1000, 11], "line-opacity": 0.45, "line-color": "#7c3aed" } });
    map!.addSource("analysis-sections", { type: "geojson", data: blank });
    map!.addLayer({ id: "analysis-section-loads", type: "line", source: "analysis-sections", paint: { "line-width": 6, "line-opacity": 0.82, "line-color": ["interpolate", ["linear"], ["get", "load_ratio"], 0, "#22c55e", 0.7, "#eab308", 1, "#f97316", 1.5, "#dc2626"] } });
    map!.addSource("analysis-stops", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    map!.addLayer({ id: "analysis-stop-loads", type: "circle", source: "analysis-stops", paint: { "circle-radius": ["interpolate", ["linear"], ["get", "boardings"], 0, 3, 100, 7, 500, 12, 1000, 18], "circle-color": "#111827", "circle-opacity": 0.72, "circle-stroke-width": 2, "circle-stroke-color": "#fff" } });
    map!.addSource("draft-route", { type: "geojson", data: emptyRouteGeoJSON() });
    map!.addLayer({ id: "draft-route-line", type: "line", source: "draft-route", paint: { "line-width": 5, "line-opacity": 0.9, "line-color": "#2563eb" } });
    map!.addSource("draft-stops", { type: "geojson", data: stopsGeoJSON() });
    map!.addLayer({ id: "draft-stop-circles", type: "circle", source: "draft-stops", paint: { "circle-radius": 6, "circle-color": "#2563eb", "circle-stroke-width": 2, "circle-stroke-color": "#fff" } });
    mapReady = true;
    mapNetworkEditor = new MapNetworkEditor(map!, { getNetwork: () => network, setNetwork: (next) => { network = next; previousNetwork = structuredClone(next); syncMapGeoJson(); render(); }, getOrigin: () => ({ lon: network.origin_lon, lat: network.origin_lat }), markDirty, onSelection: (kind, id) => { selectedTrackId = kind === "track" ? id : null; editorSelection = { kind, id }; renderPropertyPanel(); render(); } });
    mapNetworkEditor.setMode(editorMode);
    syncMapGeoJson();
  });
  map.on("click", (event) => { if (drawMode) addStop(event); });
}
function wireEditorActions(): void {
  shell.querySelector('[data-action="editor-select"]')?.addEventListener("click", () => setEditorMode("select"));
  shell.querySelector('[data-action="editor-node"]')?.addEventListener("click", () => setEditorMode("node"));
  shell.querySelector('[data-action="editor-track"]')?.addEventListener("click", () => setEditorMode("track"));
  shell.querySelector('[data-action="editor-crossover"]')?.addEventListener("click", () => setEditorMode("crossover"));
  shell.querySelector('[data-action="editor-signal"]')?.addEventListener("click", () => setEditorMode("signal"));
  shell.querySelector('[data-action="undo"]')?.addEventListener("click", () => { mapNetworkEditor?.undo(); editorSelection = { kind: null, id: null }; renderPropertyPanel(); render(); });
  shell.querySelector('[data-action="redo"]')?.addEventListener("click", () => { mapNetworkEditor?.redo(); editorSelection = { kind: null, id: null }; renderPropertyPanel(); render(); });
}

async function bootstrap(): Promise<void> {
  const defaults = {
    showRoads: true, showRoadSpeed: false, showStops: true, showPlaces: true,
    showConnectors: false, showPopulation: false, showDemandStreets: true,
    showPassengerFlow: true, showStationLoads: true,
  };
  let settings: Record<string, unknown>;
  try {
    settings = loadUiSettings(defaults);
  } catch (error) {
    setStatus(error instanceof Error ? error.message : "Ошибка чтения настроек интерфейса");
    settings = { ...defaults };
  }
  showRoads = Boolean(settings.showRoads);
  showRoadSpeed = Boolean(settings.showRoadSpeed);
  showStops = Boolean(settings.showStops);
  showPlaces = Boolean(settings.showPlaces);
  showConnectors = Boolean(settings.showConnectors);
  showPopulation = Boolean(settings.showPopulation);
  showDemandStreets = Boolean(settings.showDemandStreets);
  showPassengerFlow = Boolean(settings.showPassengerFlow);
  showStationLoads = Boolean(settings.showStationLoads);
  for (const [element, key] of toggleInputs) {
    if (key in settings) element.checked = Boolean(settings[key]);
  }
  Object.assign(initialSettings, {
    showRoads, showRoadSpeed, showStops, showPlaces, showConnectors,
    showPopulation, showDemandStreets, showPassengerFlow, showStationLoads,
  });
  modeInput.innerHTML = Object.entries(MODE_LABELS).map(([value, label]) => `<option value="${value}">${label}</option>`).join("");
  try {
    const saved = await loadProject("current");
    if (saved && typeof saved === "object") applyProject(saved as ProjectFile);
  } catch (error) {
    setStatus(
      error instanceof Error
        ? `Проект не восстановлен: ${error.message}`
        : "Проект не восстановлен: хранилище недоступно",
    );
  }
  network = buildNetworkPayload();
  initialized = true;
  render();
  initializeMap();
  void refreshEvaluation();
}
async function refreshEvaluation(): Promise<void> {
  try {
    evaluationSummary = networkCounts(network);
    render();
  } catch {
    // Local editing remains available when a worker fails.
  }
}

window.addEventListener("beforeunload", () => {
  if (autosaveTimer) clearTimeout(autosaveTimer);
  evaluationClient?.close();
  evaluationClient = null;
  disposeComputationWorkers();
  map?.remove();
});

wireEditorActions();
renderPropertyPanel();
void bootstrap();
