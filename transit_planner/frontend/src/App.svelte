<script lang="ts">
  import { onMount, onDestroy } from "svelte";
  import { Map as MapLibreMap, NavigationControl, type GeoJSONSource, type MapMouseEvent } from "maplibre-gl";
  import type { FeatureCollection, LineString, Point as GeoJSONPoint } from "geojson";
  import {
    createTimetable,
    loadOvertureNetwork,
    loadOvertureRoute,
    validateNetwork,
    calculateAssignment,
    calculateCityAssignment,
    calculateEconomics,
    loadDemandStreets,
    loadPopulationZones,
    compareScenarios,
    type ScenarioPayload,
    type OvertureNetworkResponse,
    type OvertureRouteResponse,
  } from "./api";
  import type { NetworkPayload, StopDraft, TransitMode } from "./types";
  import { loadDataset, loadProject, loadUiSettings, saveDataset, saveProject, saveUiSettings } from "./storage";
  import { disposeComputationWorkers, evaluateNetwork, runClientPreview } from "./workers";
  import MapView from "./components/MapView.svelte";
  import ControlPanel from "./components/ControlPanel.svelte";
  import NetworkView from "./components/NetworkView.svelte";
  import EvaluationPanel from "./components/EvaluationPanel.svelte";
  import { stops as stopsStore, mode as modeStore, routeName as routeNameStore, headways as headwaysStore } from "./stores/network";
  import { project as projectStore, markProjectDirty, markProjectClean } from "./stores/project";

  const DEFAULT_CENTER: [number, number] = [39.20, 51.67];
  const MAP_STYLE = import.meta.env.VITE_MAP_STYLE_URL ?? "https://tiles.openfreemap.org/styles/liberty";

  const MODE_LABELS: Record<TransitMode, string> = {
    bus: "Автобус",
    tram: "Трамвай",
    metro: "Метро",
    rail: "Железная дорога",
  };

  const MODE_CAPACITY: Record<TransitMode, number> = {
    bus: 90,
    tram: 250,
    metro: 750,
    rail: 1000,
  };

  const PERIODS = [
    { id: "early", start_minute: 240, end_minute: 360 },
    { id: "am", start_minute: 360, end_minute: 540 },
    { id: "mid", start_minute: 540, end_minute: 900 },
    { id: "pm", start_minute: 900, end_minute: 1140 },
    { id: "eve", start_minute: 1140, end_minute: 1440 },
  ];

  type ProjectFile = {
    format: "transit-planner-project";
    version: number;
    routeName: string;
    mode: TransitMode;
    headways: Record<string, number>;
    stops: StopDraft[];
    network: NetworkPayload;
    roadRoute?: FeatureCollection<LineString, object> | null;
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

  let mapContainer: HTMLDivElement;
  let mapRef: MapLibreMap | null = null;
  let mapReady = false;
  let fileInput: HTMLInputElement;

  const stopsStoreRef = stopsStore;
  const modeStoreRef = modeStore;
  const routeNameStoreRef = routeNameStore;
  const headwaysStoreRef = headwaysStore;
  let previewTrips = 1000;
  let farePerTransitTrip = 0;
  let annualDays = 365;

  let cityRoads: FeatureCollection | null = null;
  let cityConnectors: FeatureCollection | null = null;
  let cityStops: FeatureCollection | null = null;
  let cityPlaces: FeatureCollection | null = null;
  let populationZones: FeatureCollection | null = null;
  let demandStreets: FeatureCollection | null = null;
  let roadRoute: FeatureCollection<LineString, object> | null = null;

  let showRoads = true;
  let showRoadSpeed = false;
  let showStops = true;
  let showPlaces = true;
  let showConnectors = false;
  let showPassengerFlow = true;
  let showStationLoads = true;
  let showDemandStreets = true;
  let showPopulation = false;
  let drawMode = false;
  let viewMode: "map" | "network" = "map";

  let assignmentResult: Awaited<ReturnType<typeof calculateAssignment>> | null = null;
  let cityAssignmentMeta: Awaited<ReturnType<typeof calculateCityAssignment>>["data"] | null = null;
  let cityAssignmentPeriods: Awaited<ReturnType<typeof calculateCityAssignment>>["periods"] = [];
  let economicsResult: Awaited<ReturnType<typeof calculateEconomics>> | null = null;
  let scenarioBase: ProjectFile["scenarioBase"] = null;
  let scenarioComparison: Awaited<ReturnType<typeof compareScenarios>> | null = null;
  let timetable: Awaited<ReturnType<typeof createTimetable>> | null = null;

  let evaluationSummary = { lines: 0, stops: 0, dailyDepartures: 0 };
  let busy = false;
  let message = "Готово к редактированию";
  let initialized = false;

  function toLocalMeters(lon: number, lat: number, originLon: number, originLat: number) {
    const earthRadius = 6378137;
    const cosLat = Math.cos((originLat * Math.PI) / 180);
    return {
      x: ((lon - originLon) * Math.PI) / 180 * earthRadius * cosLat,
      y: ((lat - originLat) * Math.PI) / 180 * earthRadius,
    };
  }

  function buildNetworkPayload(): NetworkPayload {
    const origin = $stopsStore[0] ?? { lon: DEFAULT_CENTER[0], lat: DEFAULT_CENTER[1] };
    const metricStops = $stopsStore.map((stop) => ({
      id: stop.id,
      name: stop.name,
      location: toLocalMeters(stop.lon, stop.lat, origin.lon, origin.lat),
      is_station: false,
    }));

    const coordinates = roadRoute?.features[0]?.geometry.coordinates ?? [];
    const geometry = coordinates.length >= 2
      ? { points: coordinates.map(([lon, lat]) => toLocalMeters(lon, lat, origin.lon, origin.lat)) }
      : metricStops.length >= 2
        ? { points: metricStops.map((stop) => stop.location) }
        : null;

    const vehicleType = {
      id: `vehicle-${$modeStore}`,
      name: MODE_LABELS[$modeStore],
      mode: $modeStore,
      capacity: MODE_CAPACITY[$modeStore],
      operating_cost_per_km: 0,
    };

    return {
      origin_lon: origin.lon,
      origin_lat: origin.lat,
      stops: metricStops,
      routes: $stopsStore.length >= 2
        ? [{ id: "draft-route", name: $routeNameStore, mode: $modeStore, stop_ids: $stopsStore.map((stop) => stop.id), geometry }]
        : [],
      vehicle_types: [vehicleType],
      periods: PERIODS,
      services: $stopsStore.length >= 2
        ? [{
            id: "draft-service",
            route_id: "draft-route",
            vehicle_type_id: vehicleType.id,
            headway_by_period: { ...$headwaysStore },
          }]
        : [],
    };
  }

  let network: NetworkPayload = buildNetworkPayload();

  $: network = buildNetworkPayload();

  $: routeRows = network.routes.map((route) => {
    const service = network.services.find((item) => item.route_id === route.id);
    const vehicle = network.vehicle_types.find((item) => item.id === service?.vehicle_type_id);
    return { route, service, vehicle };
  });

  $: totalDailyDepartures = network.services.reduce((sum, service) =>
    sum + Object.entries(service.headway_by_period).reduce((periodSum, [periodId, headway]) => {
      const period = network.periods.find((item) => item.id === periodId);
      return !period || headway <= 0 ? periodSum : periodSum + Math.ceil((period.end_minute - period.start_minute) / headway);
    }, 0), 0);

  function stopsGeoJSON(): FeatureCollection<GeoJSONPoint, { id: string; name: string }> {
    return {
      type: "FeatureCollection",
      features: $stopsStore.map((stop) => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: [stop.lon, stop.lat] },
        properties: { id: stop.id, name: stop.name },
      })),
    };
  }

  function routeGeoJSON(): FeatureCollection<LineString, object> {
    return {
      type: "FeatureCollection",
      features: $stopsStore.length >= 2
        ? [{
            type: "Feature",
            geometry: { type: "LineString", coordinates: $stopsStore.map((stop) => [stop.lon, stop.lat]) },
            properties: {},
          }]
        : [],
    };
  }

  function assignmentSectionGeoJSON(): FeatureCollection<LineString, Record<string, unknown>> {
    if (!assignmentResult) return { type: "FeatureCollection", features: [] };
    const byId = new Map($stopsStore.map((stop) => [stop.id, stop]));
    return {
      type: "FeatureCollection",
      features: assignmentResult.section_loads.flatMap((section) => {
        const from = byId.get(section.from_stop_id);
        const to = byId.get(section.to_stop_id);
        return from && to
          ? [{
              type: "Feature",
              geometry: { type: "LineString", coordinates: [[from.lon, from.lat], [to.lon, to.lat]] },
              properties: section,
            }]
          : [];
      }),
    };
  }

  function assignmentStopGeoJSON(): FeatureCollection<GeoJSONPoint, Record<string, unknown>> {
    if (!assignmentResult) return { type: "FeatureCollection", features: [] };
    const byId = new Map($stopsStore.map((stop) => [stop.id, stop]));
    return {
      type: "FeatureCollection",
      features: assignmentResult.stop_flows.flatMap((flow) => {
        const stop = byId.get(flow.stop_id);
        return stop
          ? [{
              type: "Feature",
              geometry: { type: "Point", coordinates: [stop.lon, stop.lat] },
              properties: flow,
            }]
          : [];
      }),
    };
  }

  function emptyPoints(): FeatureCollection<GeoJSONPoint> {
    return { type: "FeatureCollection", features: [] };
  }

  function syncProjectStore(dirty = $projectStore.dirty) {
    projectStore.set({
      id: "current",
      network: buildNetworkPayload(),
      scenarioBase: structuredClone(scenarioBase),
      initialized,
      dirty,
    });
  }

  function handleProjectChange() {
    markProjectDirty();
    syncProjectStore(true);
  }

  function commitStops(next: StopDraft[]) {
    $stopsStore = next;
    roadRoute = null;
    assignmentResult = null;
    demandStreets = null;
    scenarioComparison = null;
    handleProjectChange();
  }

  function addStop(event: MapMouseEvent) {
    if (!drawMode) return;
    const index = $stopsStore.length + 1;
    commitStops([...$stopsStore, {
      id: `stop-${Date.now()}-${index}`,
      name: `Остановка ${index}`,
      lon: event.lngLat.lng,
      lat: event.lngLat.lat,
    }]);
  }

  function removeStop(id: string) {
    commitStops($stopsStore.filter((stop) => stop.id !== id));
  }

  function clearRoute() {
    $stopsStore = [];
    roadRoute = null;
    assignmentResult = null;
    demandStreets = null;
    scenarioComparison = null;
    economicsResult = null;
    timetable = null;
    message = "Маршрут очищен";
    handleProjectChange();
  }

  function datasetCacheKey(prefix: string, bounds: { south: number; west: number; north: number; east: number }) {
    const round = (value: number) => value.toFixed(4);
    return `${prefix}:${round(bounds.south)}:${round(bounds.west)}:${round(bounds.north)}:${round(bounds.east)}`;
  }

  async function loadCityData() {
    if (!mapRef) return;
    busy = true;
    message = "Проверка локального кэша Overture…";
    try {
      const bounds = mapRef.getBounds();
      const cacheKey = datasetCacheKey("overture-network", {
        south: bounds.getSouth(), west: bounds.getWest(), north: bounds.getNorth(), east: bounds.getEast(),
      });
      const cached = await loadDataset<OvertureNetworkResponse>(cacheKey);
      const data = cached ?? await loadOvertureNetwork(
        bounds.getSouth(), bounds.getWest(), bounds.getNorth(), bounds.getEast(),
      );
      if (!cached) await saveDataset(cacheKey, data);

      cityRoads = data.roads;
      cityConnectors = data.connectors;
      cityStops = data.stops;
      cityPlaces = data.places;

      const populationKey = datasetCacheKey("population-zones", {
        south: bounds.getSouth(), west: bounds.getWest(), north: bounds.getNorth(), east: bounds.getEast(),
      });
      const cachedPopulation = await loadDataset<FeatureCollection>(populationKey);
      if (cachedPopulation) {
        populationZones = cachedPopulation;
      } else {
        try {
          const loadedPopulation = await loadPopulationZones(
            bounds.getSouth(), bounds.getWest(), bounds.getNorth(), bounds.getEast(),
          );
          populationZones = loadedPopulation;
          await saveDataset(populationKey, loadedPopulation);
        } catch {
          populationZones = null;
        }
      }

      message = `${cached ? "Кэш Overture" : "Overture"} ${data.release}: ${data.counts.roads} участков, ${data.counts.connectors} коннекторов, ${data.counts.stops} остановок`;
    } catch (error) {
      message = error instanceof Error ? error.message : "Ошибка загрузки Overture";
    } finally {
      busy = false;
    }
  }

  async function buildRoadRoute() {
    if (!mapRef || $stopsStore.length < 2) return;
    busy = true;
    message = "Построение маршрута по Overture…";
    try {
      const bounds = mapRef.getBounds();
      const routePoints = $stopsStore.map((stop) => ({ lon: stop.lon, lat: stop.lat }));
      const routeKey = datasetCacheKey("overture-route", {
        south: bounds.getSouth(), west: bounds.getWest(), north: bounds.getNorth(), east: bounds.getEast(),
      }) + ":" + routePoints.map((point) => `${point.lon.toFixed(5)},${point.lat.toFixed(5)}`).join(";");
      const cached = await loadDataset<OvertureRouteResponse>(routeKey);
      const data = cached ?? await loadOvertureRoute(
        routePoints,
        bounds.getSouth(), bounds.getWest(), bounds.getNorth(), bounds.getEast(),
      );
      if (!cached) await saveDataset(routeKey, data);
      roadRoute = {
        type: "FeatureCollection",
        features: [{ type: "Feature", geometry: data.geometry, properties: data.properties }],
      };
      message = `${cached ? "Кэш Overture" : "Overture"} маршрут: ${(data.properties.length_m / 1000).toFixed(2)} км, ${data.properties.travel_time_min.toFixed(1)} мин`;
    } catch (error) {
      roadRoute = null;
      message = error instanceof Error ? error.message : "Ошибка построения маршрута";
    } finally {
      busy = false;
    }
  }

  function previewZones() {
    const origin = $stopsStore[0];
    if (!origin) return [];
    return $stopsStore.map((stop) => {
      const point = toLocalMeters(stop.lon, stop.lat, origin.lon, origin.lat);
      return { id: stop.id, centroid_x: point.x, centroid_y: point.y };
    });
  }

  async function runPreviewAssignment() {
    if ($stopsStore.length < 2) return;
    busy = true;
    message = "Расчёт проверочного пассажиропотока…";
    try {
      const clientPreview = await runClientPreview(network);
      evaluationSummary = clientPreview.evaluation;
      const demand = [{
        origin_zone_id: $stopsStore[0].id,
        destination_zone_id: $stopsStore[$stopsStore.length - 1].id,
        trips_per_day: previewTrips,
        purpose: "all",
      }];
      const result = await calculateAssignment(network, demand, previewZones(), "am");
      assignmentResult = result;
      message = `Локальный предрасчёт: ${clientPreview.operations.dailyDepartures} отправлений, парк до ${clientPreview.operations.fleetEstimate}.`;
      demandStreets = await loadDemandStreets(demand, previewZones(), $stopsStore[0].lon, $stopsStore[0].lat);
      message = `Пассажиропоток рассчитан: transit ${(result.metrics.transit_share * 100).toFixed(1)}%`;
    } catch (error) {
      message = error instanceof Error ? error.message : "Ошибка расчёта пассажиропотока";
    } finally {
      busy = false;
    }
  }

  async function runCityAssignment() {
    if (!mapRef || $stopsStore.length < 2) return;
    busy = true;
    message = "Расчёт городской сети по WorldPop + Overture…";
    try {
      const bounds = mapRef.getBounds();
      const result = await calculateCityAssignment(
        network,
        bounds.getSouth(), bounds.getWest(), bounds.getNorth(), bounds.getEast(),
        network.origin_lon ?? DEFAULT_CENTER[0],
        network.origin_lat ?? DEFAULT_CENTER[1],
        "am",
        Math.max(0, farePerTransitTrip),
        Math.max(1, Math.min(366, Math.round(annualDays))),
      );
      assignmentResult = result.assignment;
      cityAssignmentMeta = result.data;
      cityAssignmentPeriods = result.periods;
      economicsResult = {
        scenario_id: "citywide",
        name: "Городская сеть",
        economics: result.economics,
      };
      message = `Городской расчёт: ${result.data.od_pairs} OD-пар`;
    } catch (error) {
      message = error instanceof Error ? error.message : "Ошибка городского расчёта";
    } finally {
      busy = false;
    }
  }

  async function runEconomics() {
    if ($stopsStore.length < 2) return;
    busy = true;
    message = "Расчёт экономики…";
    try {
      const result = await calculateEconomics(
        network,
        [{
          origin_zone_id: $stopsStore[0].id,
          destination_zone_id: $stopsStore[$stopsStore.length - 1].id,
          trips_per_day: previewTrips,
          purpose: "all",
        }],
        previewZones(),
        "am",
        Math.max(0, farePerTransitTrip),
        Math.max(1, Math.min(366, Math.round(annualDays))),
      );
      economicsResult = result;
      message = "Экономика рассчитана";
    } catch (error) {
      message = error instanceof Error ? error.message : "Ошибка расчёта экономики";
    } finally {
      busy = false;
    }
  }

  function makeScenarioPayload(id: string, name: string, scenarioNetwork: NetworkPayload, origin: StopDraft, destination: StopDraft, trips: number, fare: number, days: number): ScenarioPayload {
    const originLon = scenarioNetwork.origin_lon ?? origin.lon;
    const originLat = scenarioNetwork.origin_lat ?? origin.lat;
    const toZone = (stop: StopDraft, zoneId: string) => {
      const point = toLocalMeters(stop.lon, stop.lat, originLon, originLat);
      return { id: zoneId, centroid_x: point.x, centroid_y: point.y };
    };
    return {
      id,
      name,
      network: scenarioNetwork,
      demand: [{
        origin_zone_id: "scenario-origin",
        destination_zone_id: "scenario-destination",
        trips_per_day: trips,
        purpose: "all",
      }],
      zones: [
        toZone(origin, "scenario-origin"),
        toZone(destination, "scenario-destination"),
      ],
      config: { period_id: "am", max_access_distance_m: 1500 },
      economics_config: {
        period_id: "am",
        fare_per_transit_trip: fare,
        annual_days: days,
      },
    };
  }

  function captureScenarioBase() {
    if ($stopsStore.length < 2) return;
    scenarioBase = {
      network: structuredClone(network),
      origin: { lon: $stopsStore[0].lon, lat: $stopsStore[0].lat },
      destination: { lon: $stopsStore[$stopsStore.length - 1].lon, lat: $stopsStore[$stopsStore.length - 1].lat },
      trips: previewTrips,
      farePerTransitTrip,
      annualDays,
    };
    scenarioComparison = null;
    handleProjectChange();
    message = "Базовый сценарий зафиксирован";
  }

  async function compareWithScenarioBase() {
    if (!scenarioBase || $stopsStore.length < 2) return;
    busy = true;
    message = "Сравнение базового и текущего сценариев…";
    try {
      const origin: StopDraft = { id: "origin", name: "Источник", ...scenarioBase.origin };
      const destination: StopDraft = { id: "destination", name: "Назначение", ...scenarioBase.destination };
      const result = await compareScenarios(
        makeScenarioPayload("base", "Базовый сценарий", scenarioBase.network, origin, destination, scenarioBase.trips, scenarioBase.farePerTransitTrip, scenarioBase.annualDays),
        makeScenarioPayload("alternative", "Текущий сценарий", network, origin, destination, scenarioBase.trips, farePerTransitTrip, annualDays),
      );
      scenarioComparison = result;
      message = "Сравнение сценариев завершено";
    } catch (error) {
      message = error instanceof Error ? error.message : "Ошибка сравнения сценариев";
    } finally {
      busy = false;
    }
  }

  async function generateTimetable() {
    const service = network.services[0];
    if (!service) return;
    busy = true;
    message = "Формирование расписания…";
    try {
      timetable = await createTimetable(service.id, network.periods, service.headway_by_period);
      message = "Расписание сформировано";
    } catch (error) {
      message = error instanceof Error ? error.message : "Ошибка формирования расписания";
    } finally {
      busy = false;
    }
  }

  function projectData(): ProjectFile {
    return {
      format: "transit-planner-project",
      version: 3,
      routeName: $routeNameStore,
      mode: $modeStore,
      headways: $headwaysStore,
      stops: $stopsStore,
      network,
      roadRoute,
      economics: { farePerTransitTrip, annualDays },
      scenarioBase,
    };
  }

  function exportJson() {
    const blob = new Blob([JSON.stringify(projectData(), null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "transit-network.json";
    anchor.click();
    URL.revokeObjectURL(url);
    void saveProject("current", projectData());
    markProjectClean();
    syncProjectStore(false);
    message = "JSON сети экспортирован";
  }

  function applyProject(project: ProjectFile) {
    if (project.format !== "transit-planner-project") throw new Error("Неверный формат проекта");
    const version = Number(project.version ?? 1);
    if (version < 1 || version > 3) throw new Error("Неподдерживаемая версия проекта");
    $routeNameStore = String(project.routeName ?? "Новый маршрут");
    $modeStore = (project.mode ?? "bus") as TransitMode;
    $headwaysStore = { ...$headwaysStore, ...(project.headways ?? {}) };
    $stopsStore = Array.isArray(project.stops) ? project.stops: [];
    roadRoute = version >= 3 && project.roadRoute?.type === "FeatureCollection" ? project.roadRoute : null;
    const economics = project.economics ?? {};
    farePerTransitTrip = Number.isFinite(Number(economics.farePerTransitTrip)) ? Math.max(0, Number(economics.farePerTransitTrip)) : 0;
    annualDays = Number.isFinite(Number(economics.annualDays)) ? Math.max(1, Math.min(366, Math.round(Number(economics.annualDays)))) : 365;
    scenarioBase = version >= 2 && project.scenarioBase?.network ? project.scenarioBase : null;
    assignmentResult = null;
    economicsResult = null;
    scenarioComparison = null;
    timetable = null;
    markProjectClean();
    syncProjectStore(false);
    message = "Проект загружен";
  }

  function loadJsonFile(file: File) {
    const reader = new FileReader();
    reader.onload = () => {
      try {
        applyProject(JSON.parse(String(reader.result)) as ProjectFile);
      } catch (error) {
        message = error instanceof Error ? error.message : "Не удалось загрузить проект";
      }
    };
    reader.readAsText(file);
  }

  onMount(async () => {
    const settings = loadUiSettings({
      showRoads: true,
      showRoadSpeed: false,
      showStops: true,
      showPlaces: true,
      showConnectors: false,
      showPassengerFlow: true,
      showStationLoads: true,
      showDemandStreets: true,
      showPopulation: false,
    });
    showRoads = Boolean(settings.showRoads);
    showRoadSpeed = Boolean(settings.showRoadSpeed);
    showStops = Boolean(settings.showStops);
    showPlaces = Boolean(settings.showPlaces);
    showConnectors = Boolean(settings.showConnectors);
    showPassengerFlow = Boolean(settings.showPassengerFlow);
    showStationLoads = Boolean(settings.showStationLoads);
    showDemandStreets = Boolean(settings.showDemandStreets);
    showPopulation = Boolean(settings.showPopulation);

    try {
      const saved = await loadProject("current");
      if (saved && typeof saved === "object") applyProject(saved as ProjectFile);
    } catch {
      // First launch or blocked IndexedDB.
    }

    const map = new MapLibreMap({
      container: mapContainer,
      style: MAP_STYLE,
      center: DEFAULT_CENTER,
      zoom: 11,
    });
    mapRef = map;
    map.addControl(new NavigationControl(), "top-right");

    map.on("load", () => {
      const blank = { type: "FeatureCollection", features: [] };
      map.addSource("city-roads", { type: "geojson", data: blank });
      map.addLayer({ id: "city-road-lines", type: "line", source: "city-roads", paint: { "line-color": "#9ca3af", "line-width": 1.2, "line-opacity": 0.65 } });
      map.addSource("city-connectors", { type: "geojson", data: emptyPoints() });
      map.addLayer({ id: "city-connector-circles", type: "circle", source: "city-connectors", paint: { "circle-radius": 2.5, "circle-color": "#f59e0b", "circle-opacity": 0.7 } });
      map.addSource("city-places", { type: "geojson", data: emptyPoints() });
      map.addLayer({ id: "city-place-circles", type: "circle", source: "city-places", paint: { "circle-radius": 3, "circle-color": "#8b5cf6", "circle-opacity": 0.5 } });
      map.addSource("city-stops", { type: "geojson", data: emptyPoints() });
      map.addLayer({ id: "city-stop-circles", type: "circle", source: "city-stops", paint: { "circle-radius": 3.5, "circle-color": "#6b7280", "circle-opacity": 0.65, "circle-stroke-width": 1, "circle-stroke-color": "#fff" } });
      map.addSource("population-zones", { type: "geojson", data: blank });
      map.addLayer({ id: "population-zone-points", type: "circle", source: "population-zones", paint: { "circle-radius": ["interpolate", ["linear"], ["get", "population"], 0, 2, 500, 5, 2000, 9, 5000, 15], "circle-opacity": 0.28, "circle-color": "#0f766e" } });
      map.addSource("demand-streets", { type: "geojson", data: blank });
      map.addLayer({ id: "demand-street-lines", type: "line", source: "demand-streets", paint: { "line-width": ["interpolate", ["linear"], ["get", "flow_weight"], 0, 1, 100, 3, 500, 7, 1000, 11], "line-opacity": 0.45, "line-color": "#7c3aed" } });
      map.addSource("analysis-sections", { type: "geojson", data: blank });
      map.addLayer({ id: "analysis-section-loads", type: "line", source: "analysis-sections", paint: { "line-width": 6, "line-opacity": 0.82, "line-color": ["interpolate", ["linear"], ["get", "load_ratio"], 0, "#22c55e", 0.7, "#eab308", 1, "#f97316", 1.5, "#dc2626"] } });
      map.addSource("analysis-stops", { type: "geojson", data: emptyPoints() });
      map.addLayer({ id: "analysis-stop-loads", type: "circle", source: "analysis-stops", paint: { "circle-radius": ["interpolate", ["linear"], ["get", "boardings"], 0, 3, 100, 7, 500, 12, 1000, 18], "circle-color": "#111827", "circle-opacity": 0.72, "circle-stroke-width": 2, "circle-stroke-color": "#fff" } });
      map.addSource("draft-route", { type: "geojson", data: routeGeoJSON() });
      map.addLayer({ id: "draft-route-line", type: "line", source: "draft-route", paint: { "line-width": 5, "line-opacity": 0.9, "line-color": "#2563eb" } });
      map.addSource("draft-stops", { type: "geojson", data: stopsGeoJSON() });
      map.addLayer({ id: "draft-stop-circles", type: "circle", source: "draft-stops", paint: { "circle-radius": 6, "circle-color": "#2563eb", "circle-stroke-width": 2, "circle-stroke-color": "#fff" } });
      mapReady = true;
    });
    map.on("click", addStop);
    initialized = true;
  });

  onDestroy(() => {
    disposeComputationWorkers();
    mapRef?.remove();
  });

  async function refreshEvaluation(current: NetworkPayload) {
    try {
      evaluationSummary = await evaluateNetwork(current);
    } catch {
      // Worker errors must not block map editing.
    }
  }

  $: if (initialized) {
    void refreshEvaluation(network);
  }


  $: if (initialized) {
    saveUiSettings({
      showRoads,
      showRoadSpeed,
      showStops,
      showPlaces,
      showConnectors,
      showPassengerFlow,
      showStationLoads,
      showDemandStreets,
      showPopulation,
    });
    void saveProject("current", projectData());
  }

  $: if (mapRef && mapReady) {
    const source = (id: string) => mapRef?.getSource(id) as GeoJSONSource | undefined;
    source("draft-route")?.setData(roadRoute ?? routeGeoJSON());
    source("draft-stops")?.setData(stopsGeoJSON());
    if (cityRoads) source("city-roads")?.setData(cityRoads as any);
    if (cityConnectors) source("city-connectors")?.setData(cityConnectors as any);
    if (cityStops) source("city-stops")?.setData(cityStops as any);
    if (cityPlaces) source("city-places")?.setData(cityPlaces as any);
    if (populationZones) source("population-zones")?.setData(populationZones as any);
    if (demandStreets) source("demand-streets")?.setData(demandStreets as any);
    source("analysis-sections")?.setData(assignmentSectionGeoJSON() as any);
    source("analysis-stops")?.setData(assignmentStopGeoJSON() as any);

    const setVisibility = (id: string, visible: boolean) => {
      if (mapRef?.getLayer(id)) mapRef.setLayoutProperty(id, "visibility", visible ? "visible" : "none");
    };
    setVisibility("city-road-lines", showRoads);
    setVisibility("city-connector-circles", showConnectors);
    setVisibility("city-stop-circles", showStops);
    setVisibility("city-place-circles", showPlaces);
    setVisibility("population-zone-points", showPopulation);
    setVisibility("demand-street-lines", showDemandStreets);
    setVisibility("analysis-section-loads", showPassengerFlow);
    setVisibility("analysis-stop-loads", showStationLoads);
    if (mapRef.getLayer("city-road-lines")) {
      mapRef.setPaintProperty("city-road-lines", "line-color",
        showRoadSpeed
          ? ["interpolate", ["linear"], ["get", "speed_kph"], 10, "#ef4444", 30, "#eab308", 50, "#22c55e", 90, "#3b82f6"]
          : "#9ca3af");
    }
  }

  function pct(value: number) {
    return (value * 100).toFixed(1);
  }
</script>

<svelte:head>
  <title>Transit Planner</title>
  <meta name="description" content="Проектирование и моделирование транспортной сети" />
</svelte:head>

<div class="app-shell">
  <header class="topbar">
    <div>
      <div class="brand">Transit Planner</div>
      <div class="subtitle">Проектирование транспортной сети</div>
    </div>
    <div class="actions">
      <button class:active-toggle={viewMode === "map"} on:click={() => viewMode = "map"}>Карта</button>
      <button class:active-toggle={viewMode === "network"} on:click={() => viewMode = "network"}>Сеть</button>
      <button on:click={loadCityData} disabled={busy}>Загрузить Overture</button>
      <button on:click={buildRoadRoute} disabled={busy || $stopsStore.length < 2}>Построить по дорогам</button>
      <button class="primary" class:active={drawMode} on:click={() => drawMode = !drawMode}>
        {drawMode ? "Завершить рисование" : "Добавить остановки"}
      </button>
      <button on:click={async () => {
        busy = true;
        try {
          const result = await validateNetwork(network);
          message = result.valid ? "Сеть корректна" : `Ошибки: ${result.errors.join("; ")}`;
        } catch (error) {
          message = error instanceof Error ? error.message : "Ошибка проверки";
        } finally {
          busy = false;
        }
      }} disabled={busy || $stopsStore.length < 2}>Проверить сеть</button>
      <button on:click={captureScenarioBase} disabled={busy || $stopsStore.length < 2}>Зафиксировать базовый</button>
      <button on:click={compareWithScenarioBase} disabled={busy || !scenarioBase || $stopsStore.length < 2}>Сравнить</button>
      <button on:click={exportJson}>Сохранить</button>
      <button on:click={() => fileInput?.click()}>Открыть</button>
      <input bind:this={fileInput} type="file" accept="application/json" hidden on:change={(event) => {
        const file = (event.currentTarget as HTMLInputElement).files?.[0];
        if (file) loadJsonFile(file);
        (event.currentTarget as HTMLInputElement).value = "";
      }} />
    </div>
  </header>

  <div class="workspace">
    <ControlPanel
      bind:routeName={$routeNameStore}
      bind:mode={$modeStore}
      modeLabels={MODE_LABELS}
      bind:previewTrips
      bind:farePerTransitTrip
      bind:annualDays
      periods={PERIODS}
      bind:headways={$headwaysStore}
      stops={$stopsStore}
      busy={busy}
      message={message}
      bind:showRoads
      bind:showRoadSpeed
      bind:showStops
      bind:showPlaces
      bind:showConnectors
      bind:showPopulation
      bind:showDemandStreets
      bind:showPassengerFlow
      bind:showStationLoads
      drawMode={drawMode}
      onPreview={runPreviewAssignment}
      onEconomics={runEconomics}
      onCityAssignment={runCityAssignment}
      onRemoveStop={removeStop}
      onClear={clearRoute}
    />

    <main class="main-panel">
      {#if viewMode === "map"}
        <MapView bind:mapElement={mapContainer} drawMode={drawMode} />
      {:else}
        <NetworkView
          summary={evaluationSummary}
          routeRows={routeRows}
          periods={PERIODS}
          modeLabels={MODE_LABELS}
          onGenerateTimetable={generateTimetable}
        />
        <EvaluationPanel
          cityMeta={cityAssignmentMeta}
          periodsData={cityAssignmentPeriods}
          assignment={assignmentResult}
          economics={economicsResult}
          scenario={scenarioComparison}
          timetable={timetable}
        />
      {/if}
    </main>
  </div>
</div>

<style>
  :global(html, body, #app) { height: 100%; margin: 0; }
  :global(body) { font-family: Inter, ui-sans-serif, system-ui, sans-serif; background: #f3f4f6; color: #111827; }
  :global(button), :global(input), :global(select) { font: inherit; }
  .app-shell { min-height: 100%; display: flex; flex-direction: column; }
  .topbar { display: flex; gap: 16px; justify-content: space-between; align-items: center; padding: 12px 16px; background: #111827; color: #fff; }
  .brand { font-size: 18px; font-weight: 800; }.subtitle { font-size: 12px; opacity: .7; margin-top: 2px; }
  .actions { display: flex; gap: 6px; flex-wrap: wrap; justify-content: flex-end; }
  button { border: 1px solid #d1d5db; background: #fff; color: #111827; padding: 7px 10px; border-radius: 7px; cursor: pointer; }
  button:hover:not(:disabled) { background: #f9fafb; } button:disabled { opacity: .5; cursor: wait; }
  button.primary { background: #2563eb; border-color: #2563eb; color: #fff; }.active-toggle, button.active { outline: 2px solid #60a5fa; outline-offset: 1px; }
  .workspace { flex: 1; min-height: 0; display: grid; grid-template-columns: 320px minmax(0, 1fr); }
  .sidebar { overflow: auto; padding: 14px; background: #fff; border-right: 1px solid #e5e7eb; }.sidebar section { padding-bottom: 16px; margin-bottom: 16px; border-bottom: 1px solid #e5e7eb; }
  .section-title { display: flex; justify-content: space-between; align-items: center; gap: 8px; font-weight: 750; margin-bottom: 10px; }
  label { display: grid; gap: 5px; font-size: 12px; margin-bottom: 9px; } label input, label select { width: 100%; box-sizing: border-box; border: 1px solid #d1d5db; border-radius: 7px; padding: 7px 8px; background: #fff; }
  .check { display: flex; grid-template-columns: auto 1fr; align-items: center; gap: 8px; font-size: 13px; }.check input { width: auto; }
  .stop-row { display: flex; gap: 8px; align-items: center; justify-content: space-between; padding: 8px; border: 1px solid #e5e7eb; border-radius: 7px; margin-bottom: 6px; }.stop-row small { display: block; color: #6b7280; margin-top: 2px; }
  .empty { color: #6b7280; font-size: 13px; line-height: 1.4; }.status { position: sticky; bottom: 0; padding: 10px; background: #f9fafb; border-radius: 7px; font-size: 12px; }.status.busy { color: #1d4ed8; }
  .main-panel { min-width: 0; min-height: 0; position: relative; }.map-wrap, .map { width: 100%; height: 100%; min-height: 640px; }.map-hint { position: absolute; top: 12px; left: 12px; padding: 8px 10px; background: rgba(17,24,39,.9); color: #fff; border-radius: 7px; font-size: 12px; }
  .network-view { height: 100%; overflow: auto; padding: 20px; box-sizing: border-box; }.network-header { display: flex; justify-content: space-between; gap: 20px; align-items: flex-start; margin-bottom: 16px; }.network-header h2 { margin: 0 0 4px; }.network-header p { color: #6b7280; margin: 0 0 10px; }
  .network-kpis, .kpi-grid { display: grid; grid-template-columns: repeat(3, minmax(110px, 1fr)); gap: 8px; }.kpi-grid { grid-template-columns: repeat(4, minmax(110px, 1fr)); }.network-kpis > div, .kpi-grid > div { background: #fff; border: 1px solid #e5e7eb; padding: 10px; border-radius: 8px; }.network-kpis span, .kpi-grid span { display: block; color: #6b7280; font-size: 11px; }.network-kpis b, .kpi-grid b { display: block; margin-top: 4px; }
  .network-empty, .analytics-panel { background: #fff; border: 1px solid #e5e7eb; border-radius: 8px; padding: 14px; margin-bottom: 12px; }.analytics-panel .section-title { margin-bottom: 12px; }
  .table-wrap { overflow: auto; background: #fff; border: 1px solid #e5e7eb; border-radius: 8px; margin-bottom: 12px; } table { width: 100%; border-collapse: collapse; font-size: 12px; } th, td { text-align: left; padding: 8px 9px; border-bottom: 1px solid #f0f0f0; white-space: nowrap; } th { background: #f9fafb; }
  .period-card { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; padding: 8px 0; border-bottom: 1px solid #f0f0f0; font-size: 12px; }.period-card:last-child { border-bottom: 0; }.period-card span { color: #4b5563; }.loss-list { margin-top: 10px; }.loss-row { display: flex; justify-content: space-between; padding: 5px 0; font-size: 12px; }.small-label { color: #6b7280; font-size: 11px; margin: 8px 0; }
  @media (max-width: 1000px) { .workspace { grid-template-columns: 280px minmax(0,1fr); }.topbar { align-items: flex-start; }.network-kpis { grid-template-columns: 1fr; }.kpi-grid { grid-template-columns: repeat(2, minmax(100px, 1fr)); } }
  @media (max-width: 760px) { .workspace { grid-template-columns: 1fr; }.sidebar { max-height: 42vh; border-right: 0; border-bottom: 1px solid #e5e7eb; }.map-wrap, .map { min-height: 58vh; }.topbar { flex-direction: column; }.actions { justify-content: flex-start; } }
</style>
