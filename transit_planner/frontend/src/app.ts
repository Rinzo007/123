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
  loadOvertureRoute,
  loadOvertureUrbanMultipliers,
  loadPopulationZones,
  loadReferenceDemand,
  validateNetwork,
  type EconomicsResult,
  type OvertureNetworkResponse,
  type OvertureRouteResponse,
  type ScenarioPayload,
  type UrbanMultipliersResponse,
} from "./api";
import type { NetworkPayload, StopDraft, TransitMode } from "./types";
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
import { createEvaluationClient, disposeComputationWorkers, runClientPreview } from "./workers";
import { runRuntimePreview } from "./workers/reference-runtime";
import { decodeLines, encodeLines } from "./line-cache";
import {
  changedSegments,
  keepNetwork,
  planningPreview,
  probe,
  segmentSignatures,
  type PlanningPreview,
} from "./simulation/preview";

const DEFAULT_CENTER: [number, number] = [39.2, 51.67];
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
let cityRoads: FeatureCollection | null = null;
let cityConnectors: FeatureCollection | null = null;
let cityStops: FeatureCollection | null = null;
let cityPlaces: FeatureCollection | null = null;
let populationZones: FeatureCollection | null = null;
let demandStreets: FeatureCollection | null = null;
let urbanMultipliers: UrbanMultipliersResponse | null = null;
let urbanMultipliersKey = "";
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
let referenceEvaluationClient: ReturnType<typeof createEvaluationClient> | null = null;
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
      <button data-action="view-network">Сеть</button>
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
  const origin = stops[0] ?? { lon: DEFAULT_CENTER[0], lat: DEFAULT_CENTER[1] };
  const metricStops = stops.map((stop) => ({
    id: stop.id,
    name: stop.name,
    location: toLocalMeters(stop.lon, stop.lat, origin.lon, origin.lat),
    is_station: false,
  }));

  const coordinates = roadRoute?.features[0]?.geometry.coordinates ?? [];
  const geometry = coordinates.length >= 2
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
      track_type: mode === "metro" ? "tunnel" : "surface",
      capacity_departures_per_hour: mode === "bus" ? 60 : 30,
      shared_group: null,
      station_ids: [from.id, to.id],
      speed_limit_kph: mode === "bus" ? 50 : mode === "tram" ? 50 : mode === "metro" ? 80 : 120,
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
    track_sections: trackSections,
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
      car_cost: 0,
      train_operating_cost_per_hour: 0,
      car_operating_cost_per_hour: 0,
      track_maintenance_cost_per_km_year: 0,
      station_maintenance_cost_per_year: 0,
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
let network = buildNetworkPayload();

function syncMapGeoJson(): void {
  if (!map || !mapReady) return;
  const source = (id: string) => map!.getSource(id) as GeoJSONSource | undefined;
  source("draft-route")?.setData(roadRoute ?? routeGeoJSON());
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
function routeGeoJSON(): FeatureCollection<LineString, object> {
  return {
    type: "FeatureCollection",
    features: stops.length >= 2 ? [{
      type: "Feature",
      geometry: { type: "LineString", coordinates: stops.map((stop) => [stop.lon, stop.lat]) },
      properties: {},
    }] : [],
  };
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

async function ensureUrbanMultipliers(current: NetworkPayload, b: Bounds): Promise<UrbanMultipliersResponse | null> {
  const key = datasetCacheKey("overture-urban", b) + ":" + current.routes.map((route) =>
    `${route.id}:${route.stop_ids.join(",")}:${JSON.stringify(route.geometry)}`,
  ).join("|");
  if (urbanMultipliersKey === key) return urbanMultipliers;
  const cached = await loadDataset<UrbanMultipliersResponse>(key);
  if (cached) {
    urbanMultipliers = cached;
    urbanMultipliersKey = key;
    return cached;
  }
  try {
    const loaded = await loadOvertureUrbanMultipliers(current, b.south, b.west, b.north, b.east);
    urbanMultipliers = loaded;
    urbanMultipliersKey = key;
    await saveDataset(key, loaded);
    return loaded;
  } catch {
    urbanMultipliers = null;
    urbanMultipliersKey = key;
    return null;
  }
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
    urbanMultipliers = null;
    urbanMultipliersKey = "";
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
  setStatus("Построение маршрута по Overture…");
  try {
    const b = bounds();
    if (!b) throw new Error("Карта ещё не готова");
    const points = stops.map((stop) => ({ lon: stop.lon, lat: stop.lat }));
    const key = datasetCacheKey("overture-route", b) + ":" + points.map((p) => `${p.lon.toFixed(5)},${p.lat.toFixed(5)}`).join(";");
    const cached = await loadDataset<OvertureRouteResponse>(key);
    const data = cached ?? await loadOvertureRoute(points, b.south, b.west, b.north, b.east);
    if (!cached) await saveDataset(key, data);
    roadRoute = {
      type: "FeatureCollection",
      features: [{ type: "Feature", geometry: data.geometry, properties: data.properties }],
    };
    network = buildNetworkPayload();
    setStatus(`${cached ? "Кэш Overture" : "Overture"} маршрут: ${(data.properties.length_m / 1000).toFixed(2)} км, ${data.properties.travel_time_min.toFixed(1)} мин`);
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
    const clientPreview = await runClientPreview(network);
    evaluationSummary = clientPreview.evaluation;
    if (!referenceEvaluationClient) referenceEvaluationClient = createEvaluationClient();
    const b = bounds();
    const referenceDemand = b ? await loadReferenceDemand(
      b.south, b.west, b.north, b.east, network.origin_lon ?? DEFAULT_CENTER[0], network.origin_lat ?? DEFAULT_CENTER[1],
    ) : undefined;
    const urban = b && referenceDemand ? await ensureUrbanMultipliers(network, b) : null;
    await runRuntimePreview(referenceEvaluationClient, network, referenceDemand, urban);
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
      network.origin_lon ?? DEFAULT_CENTER[0], network.origin_lat ?? DEFAULT_CENTER[1],
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
    const b = bounds();
    const urban = b ? await ensureUrbanMultipliers(network, b) : null;
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
      urban,
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
  const lon0 = scenarioNetwork.origin_lon ?? origin.lon;
  const lat0 = scenarioNetwork.origin_lat ?? origin.lat;
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
  if (cityAssignmentMeta) {
    html += `<section class="analytics-panel"><h3>Городской расчёт</h3>${metricGrid([
      ["Зоны", cityAssignmentMeta.zones],
      ["Places", cityAssignmentMeta.places],
      ["OD-пары", cityAssignmentMeta.od_pairs],
      ["Спрос/сутки", cityAssignmentMeta.total_demand_trips],
    ])}</section>`;
  }
  if (urbanMultipliers) {
    html += `<section class="analytics-panel"><h3>Городской контекст Overture</h3>${metricGrid([
      ["Здания", urbanMultipliers.counts.buildings],
      ["Вода", urbanMultipliers.counts.water],
      ["Сегменты", urbanMultipliers.counts.segments],
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
      ["CAPEX", e.capital_cost],
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

shell.addEventListener("click", (event) => {
  const target = event.target as HTMLElement;
  const action = target.closest<HTMLElement>("[data-action]")?.dataset.action;
  if (target.closest("[data-remove-stop]")) {
    removeStop((target.closest("[data-remove-stop]") as HTMLElement).dataset.removeStop!);
    return;
  }
  switch (action) {
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
    case "timetable": void generateTimetable(); break;
    case "validate":
      busy = true;
      void validateNetwork(network).then((result) => setStatus(result.valid ? "Сеть корректна" : `Ошибки: ${result.errors.join("; ")}`))
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
  if (event.key !== "Escape" || !busy) return;
  referenceEvaluationClient?.cancel();
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
  map = new MapLibreMap({ container: mapElement, style: MAP_STYLE, center: DEFAULT_CENTER, zoom: 11 });
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
    map!.addSource("draft-route", { type: "geojson", data: routeGeoJSON() });
    map!.addLayer({ id: "draft-route-line", type: "line", source: "draft-route", paint: { "line-width": 5, "line-opacity": 0.9, "line-color": "#2563eb" } });
    map!.addSource("draft-stops", { type: "geojson", data: stopsGeoJSON() });
    map!.addLayer({ id: "draft-stop-circles", type: "circle", source: "draft-stops", paint: { "circle-radius": 6, "circle-color": "#2563eb", "circle-stroke-width": 2, "circle-stroke-color": "#fff" } });
    mapReady = true;
    syncMapGeoJson();
  });
  map.on("click", addStop);
}
async function bootstrap(): Promise<void> {
  const settings = loadUiSettings({
    showRoads: true, showRoadSpeed: false, showStops: true, showPlaces: true,
    showConnectors: false, showPopulation: false, showDemandStreets: true,
    showPassengerFlow: true, showStationLoads: true,
  });
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
  } catch {
    // first launch or blocked IndexedDB
  }
  network = buildNetworkPayload();
  initialized = true;
  render();
  initializeMap();
  void refreshEvaluation();
}
async function refreshEvaluation(): Promise<void> {
  try {
    evaluationSummary = await runClientPreview(network).then((result) => result.evaluation);
    render();
  } catch {
    // Local editing remains available when a worker fails.
  }
}

window.addEventListener("beforeunload", () => {
  if (autosaveTimer) clearTimeout(autosaveTimer);
  referenceEvaluationClient?.close();
  referenceEvaluationClient = null;
  disposeComputationWorkers();
  map?.remove();
});

void bootstrap();
