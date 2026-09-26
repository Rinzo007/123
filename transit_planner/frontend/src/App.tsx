import { useEffect, useMemo, useRef, useState } from "react";
import { Map as MapLibreMap, NavigationControl, type GeoJSONSource, type MapMouseEvent } from "maplibre-gl";
import {
  createTimetable,
  loadOvertureNetwork,
  loadOvertureRoute,
  validateNetwork,
  calculateAssignment,
  calculateCityAssignment,
  loadDemandStreets,
  loadPopulationZones,
} from "./api";
import type { FeatureCollection, LineString, Point as GeoJSONPoint } from "geojson";
import type { NetworkPayload, StopDraft, TransitMode } from "./types";

const DEFAULT_CENTER: [number, number] = [39.20, 51.67];
const MAP_STYLE =
  import.meta.env.VITE_MAP_STYLE_URL ??
  "https://demotiles.maplibre.org/style.json";

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

function toLocalMeters(
  lon: number,
  lat: number,
  originLon: number,
  originLat: number,
): { x: number; y: number } {
  const earthRadius = 6378137;
  const cosLat = Math.cos((originLat * Math.PI) / 180);
  return {
    x: ((lon - originLon) * Math.PI) / 180 * earthRadius * cosLat,
    y: ((lat - originLat) * Math.PI) / 180 * earthRadius,
  };
}

function buildNetworkPayload(
  stops: StopDraft[],
  mode: TransitMode,
  routeName: string,
  headways: Record<string, number>,
): NetworkPayload {
  const origin = stops[0] ?? {
    lon: DEFAULT_CENTER[0],
    lat: DEFAULT_CENTER[1],
  };

  const metricStops = stops.map((stop) => ({
    id: stop.id,
    name: stop.name,
    location: toLocalMeters(stop.lon, stop.lat, origin.lon, origin.lat),
    is_station: false,
  }));

  const route = {
    id: "draft-route",
    name: routeName,
    mode,
    stop_ids: stops.map((stop) => stop.id),
    geometry:
      metricStops.length >= 2
        ? { points: metricStops.map((stop) => stop.location) }
        : null,
  };

  const vehicleType = {
    id: `vehicle-${mode}`,
    name: MODE_LABELS[mode],
    mode,
    capacity: MODE_CAPACITY[mode],
    operating_cost_per_km: 0,
  };

  return {
    origin_lon: origin.lon,
    origin_lat: origin.lat,
    stops: metricStops,
    routes: stops.length >= 2 ? [route] : [],
    vehicle_types: [vehicleType],
    periods: [
      { id: "early", start_minute: 240, end_minute: 360 },
      { id: "am", start_minute: 360, end_minute: 540 },
      { id: "mid", start_minute: 540, end_minute: 900 },
      { id: "pm", start_minute: 900, end_minute: 1140 },
      { id: "eve", start_minute: 1140, end_minute: 1440 },
    ],
    services:
      stops.length >= 2
        ? [
            {
              id: "draft-service",
              route_id: "draft-route",
              vehicle_type_id: vehicleType.id,
              headway_by_period: {
                early: headways.early,
                am: headways.am,
                mid: headways.mid,
                pm: headways.pm,
                eve: headways.eve,
              },
            },
          ]
        : [],
  };
}


function assignmentSectionGeoJSON(
  result: { section_loads: Array<{ route_id: string; from_stop_id: string; to_stop_id: string; passengers: number; capacity: number; load_ratio: number; crowding_level: string }> },
  stops: StopDraft[],
): FeatureCollection<LineString, { route_id: string; from_stop_id: string; to_stop_id: string; passengers: number; capacity: number; load_ratio: number }> {
  const byId = new Map(stops.map((stop) => [stop.id, stop]));
  return {
    type: "FeatureCollection",
    features: result.section_loads.flatMap((section) => {
      const from = byId.get(section.from_stop_id);
      const to = byId.get(section.to_stop_id);
      if (!from || !to) return [];
      return [{
        type: "Feature",
        geometry: { type: "LineString", coordinates: [[from.lon, from.lat], [to.lon, to.lat]] },
        properties: section,
      }];
    }),
  };
}

function assignmentStopGeoJSON(
  result: { stop_flows: Array<{ stop_id: string; boardings: number; alightings: number; transfers: number; dwell_seconds: number; platform_m: number }> },
  stops: StopDraft[],
) : FeatureCollection<GeoJSONPoint, { stop_id: string; boardings: number; alightings: number; transfers: number }> {
  const byId = new Map(stops.map((stop) => [stop.id, stop]));
  return {
    type: "FeatureCollection",
    features: result.stop_flows.flatMap((flow) => {
      const stop = byId.get(flow.stop_id);
      if (!stop) return [];
      return [{ type: "Feature", geometry: { type: "Point", coordinates: [stop.lon, stop.lat] }, properties: flow }];
    }),
  };
}
function emptyPointCollection(): FeatureCollection<GeoJSONPoint, { id: string; name?: string }> {
  return { type: "FeatureCollection", features: [] };
}

function stopsGeoJSON(
  stops: StopDraft[],
): FeatureCollection<GeoJSONPoint, { id: string; name: string }> {
  return {
    type: "FeatureCollection",
    features: stops.map((stop) => ({
      type: "Feature",
      geometry: { type: "Point", coordinates: [stop.lon, stop.lat] },
      properties: { id: stop.id, name: stop.name },
    })),
  };
}

function routeGeoJSON(
  stops: StopDraft[],
): FeatureCollection<LineString, object> {
  return {
    type: "FeatureCollection",
    features:
      stops.length >= 2
        ? [
            {
              type: "Feature",
              geometry: {
                type: "LineString",
                coordinates: stops.map((stop) => [stop.lon, stop.lat]),
              },
              properties: {},
            },
          ]
        : [],
  };
}

export function App() {
  const mapContainer = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const loadInputRef = useRef<HTMLInputElement | null>(null);
  const drawModeRef = useRef(false);
  const [drawMode, setDrawMode] = useState(false);
  const [stops, setStops] = useState<StopDraft[]>([]);
  const [cityRoads, setCityRoads] = useState<FeatureCollection | null>(null);
  const [cityConnectors, setCityConnectors] = useState<FeatureCollection | null>(null);
  const [cityStops, setCityStops] = useState<FeatureCollection | null>(null);
  const [cityPlaces, setCityPlaces] = useState<FeatureCollection | null>(null);
  const [roadRoute, setRoadRoute] = useState<FeatureCollection<LineString, object> | null>(null);
  const [mode, setMode] = useState<TransitMode>("bus");
  const [routeName, setRouteName] = useState("Новый маршрут");
  const [headways, setHeadways] = useState<Record<string, number>>({ early: 20, am: 10, mid: 12, pm: 10, eve: 20 });
  const [message, setMessage] = useState("Готово к редактированию");
  const [busy, setBusy] = useState(false);
  const [viewMode, setViewMode] = useState<"map" | "network">("map");
  const [showRoads, setShowRoads] = useState(true);
  const [showRoadSpeed, setShowRoadSpeed] = useState(false);
  const [showStops, setShowStops] = useState(true);
  const [showPlaces, setShowPlaces] = useState(true);
  const [showConnectors, setShowConnectors] = useState(false);
  const [showPassengerFlow, setShowPassengerFlow] = useState(true);
  const [showStationLoads, setShowStationLoads] = useState(true);
  const [previewTrips, setPreviewTrips] = useState(1000);
  const [assignmentResult, setAssignmentResult] = useState<Awaited<ReturnType<typeof calculateAssignment>> | null>(null);
  const [cityAssignmentMeta, setCityAssignmentMeta] = useState<Awaited<ReturnType<typeof calculateCityAssignment>>["data"] | null>(null);
  const [demandStreets, setDemandStreets] = useState<FeatureCollection | null>(null);
  const [showDemandStreets, setShowDemandStreets] = useState(true);
  const [populationZones, setPopulationZones] = useState<FeatureCollection | null>(null);
  const [showPopulation, setShowPopulation] = useState(false);
  const [timetable, setTimetable] = useState<{ service_id: string; periods: Array<{ period_id: string; departures_minute: number[] }> } | null>(null);
  const [stopHistory, setStopHistory] = useState<StopDraft[][]>([]);
  const [stopFuture, setStopFuture] = useState<StopDraft[][]>([]);

  useEffect(() => {
    drawModeRef.current = drawMode;
  }, [drawMode]);

  useEffect(() => {
    if (!mapContainer.current) return;

    const map = new MapLibreMap({
      container: mapContainer.current,
      style: MAP_STYLE,
      center: DEFAULT_CENTER,
      zoom: 11,
    });

    mapRef.current = map;
    map.addControl(new NavigationControl(), "top-right");

    const handleMapClick = (event: MapMouseEvent) => {
      if (!drawModeRef.current) return;

      const id = `stop-${Date.now()}-${Math.round(event.lngLat.lng * 1000)}`;
      setRoadRoute(null);
      commitStops((current) => [...current, { id, name: `Остановка ${current.length + 1}`, lon: event.lngLat.lng, lat: event.lngLat.lat }]);
    };

    const handleLoad = () => {
      map.addSource("city-roads", {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });
      map.addLayer({
        id: "city-road-lines",
        type: "line",
        source: "city-roads",
        paint: {
          "line-color": "#9ca3af",
          "line-width": 1.2,
          "line-opacity": 0.65,
        },
      });

      map.addSource("city-connectors", {
        type: "geojson",
        data: emptyPointCollection(),
      });
      map.addLayer({
        id: "city-connector-circles",
        type: "circle",
        source: "city-connectors",
        paint: {
          "circle-radius": 2.5,
          "circle-color": "#f59e0b",
          "circle-opacity": 0.7,
        },
      });

      map.addSource("city-places", {
        type: "geojson",
        data: emptyPointCollection(),
      });
      map.addLayer({
        id: "city-place-circles",
        type: "circle",
        source: "city-places",
        paint: {
          "circle-radius": 3,
          "circle-color": "#8b5cf6",
          "circle-opacity": 0.5,
        },
      });

      map.addSource("city-stops", {
        type: "geojson",
        data: emptyPointCollection(),
      });
      map.addLayer({
        id: "city-stop-circles",
        type: "circle",
        source: "city-stops",
        paint: {
          "circle-radius": 3.5,
          "circle-color": "#6b7280",
          "circle-opacity": 0.65,
          "circle-stroke-width": 1,
          "circle-stroke-color": "#ffffff",
        },
      });

      map.addSource("population-zones", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.addLayer({
        id: "population-zone-points",
        type: "circle",
        source: "population-zones",
        paint: {
          "circle-radius": ["interpolate", ["linear"], ["get", "population"], 0, 2, 500, 5, 2000, 9, 5000, 15],
          "circle-opacity": 0.28,
          "circle-color": "#0f766e",
        },
      });

      map.addSource("demand-streets", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.addLayer({
        id: "demand-street-lines",
        type: "line",
        source: "demand-streets",
        paint: {
          "line-width": ["interpolate", ["linear"], ["get", "flow_weight"], 0, 1, 100, 3, 500, 7, 1000, 11],
          "line-opacity": 0.45,
          "line-color": "#7c3aed",
        },
      });

      map.addSource("analysis-sections", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.addLayer({
        id: "analysis-section-loads",
        type: "line",
        source: "analysis-sections",
        paint: {
          "line-width": 6,
          "line-opacity": 0.82,
          "line-color": [
            "interpolate", ["linear"], ["get", "load_ratio"],
            0, "#22c55e",
            0.7, "#eab308",
            1.0, "#f97316",
            1.5, "#dc2626",
          ],
        },
      });

      map.addSource("analysis-stops", { type: "geojson", data: emptyPointCollection() });
      map.addLayer({
        id: "analysis-stop-loads",
        type: "circle",
        source: "analysis-stops",
        paint: {
          "circle-radius": ["interpolate", ["linear"], ["get", "boardings"], 0, 3, 100, 7, 500, 12, 1000, 18],
          "circle-color": "#111827",
          "circle-opacity": 0.72,
          "circle-stroke-width": 2,
          "circle-stroke-color": "#ffffff",
        },
      });

      map.addSource("draft-route", {
        type: "geojson",
        data: routeGeoJSON([]),
      });
      map.addLayer({
        id: "draft-route-line",
        type: "line",
        source: "draft-route",
        paint: {
          "line-width": 5,
          "line-opacity": 0.9,
          "line-color": "#2563eb",
        },
      });

      map.addSource("draft-stops", {
        type: "geojson",
        data: stopsGeoJSON([]),
      });
      map.addLayer({
        id: "draft-stop-circles",
        type: "circle",
        source: "draft-stops",
        paint: {
          "circle-radius": 6,
          "circle-color": "#2563eb",
          "circle-stroke-width": 2,
          "circle-stroke-color": "#ffffff",
        },
      });
    };

    map.on("load", handleLoad);
    map.on("click", handleMapClick);

    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map?.isStyleLoaded()) return;

    const routeSource = map.getSource("draft-route") as GeoJSONSource | undefined;
    const draftStopsSource = map.getSource("draft-stops") as GeoJSONSource | undefined;
    const cityRoadSource = map.getSource("city-roads") as GeoJSONSource | undefined;
    const cityConnectorSource = map.getSource("city-connectors") as GeoJSONSource | undefined;
    const cityStopSource = map.getSource("city-stops") as GeoJSONSource | undefined;
    const cityPlaceSource = map.getSource("city-places") as GeoJSONSource | undefined;
    const populationSource = map.getSource("population-zones") as GeoJSONSource | undefined;
    const demandStreetSource = map.getSource("demand-streets") as GeoJSONSource | undefined;
    const analysisSectionSource = map.getSource("analysis-sections") as GeoJSONSource | undefined;
    const analysisStopSource = map.getSource("analysis-stops") as GeoJSONSource | undefined;

    routeSource?.setData(roadRoute ?? routeGeoJSON(stops));
    draftStopsSource?.setData(stopsGeoJSON(stops));
    if (cityRoads) cityRoadSource?.setData(cityRoads);
    if (cityConnectors) cityConnectorSource?.setData(cityConnectors);
    if (cityStops) cityStopSource?.setData(cityStops);
    if (cityPlaces) cityPlaceSource?.setData(cityPlaces);
    if (populationZones) populationSource?.setData(populationZones);
    if (demandStreets) demandStreetSource?.setData(demandStreets);
    if (map.getLayer("population-zone-points")) map.setLayoutProperty("population-zone-points", "visibility", showPopulation ? "visible" : "none");
    if (map.getLayer("demand-street-lines")) map.setLayoutProperty("demand-street-lines", "visibility", showDemandStreets ? "visible" : "none");
    if (assignmentResult) {
      analysisSectionSource?.setData(assignmentSectionGeoJSON(assignmentResult, stops));
      analysisStopSource?.setData(assignmentStopGeoJSON(assignmentResult, stops));
    } else {
      analysisSectionSource?.setData({ type: "FeatureCollection", features: [] });
      analysisStopSource?.setData(emptyPointCollection());
    }
    if (map.getLayer("analysis-section-loads")) map.setLayoutProperty("analysis-section-loads", "visibility", showPassengerFlow ? "visible" : "none");
    if (map.getLayer("analysis-stop-loads")) map.setLayoutProperty("analysis-stop-loads", "visibility", showStationLoads ? "visible" : "none");

    if (map.getLayer("city-road-lines")) {
      map.setLayoutProperty("city-road-lines", "visibility", showRoads ? "visible" : "none");
      map.setPaintProperty(
        "city-road-lines",
        "line-color",
        showRoadSpeed
          ? ["interpolate", ["linear"], ["get", "speed_kph"], 10, "#ef4444", 30, "#eab308", 50, "#22c55e", 90, "#3b82f6"]
          : "#9ca3af",
      );
    }
    if (map.getLayer("city-connector-circles")) map.setLayoutProperty("city-connector-circles", "visibility", showConnectors ? "visible" : "none");
    if (map.getLayer("city-stop-circles")) map.setLayoutProperty("city-stop-circles", "visibility", showStops ? "visible" : "none");
    if (map.getLayer("city-place-circles")) map.setLayoutProperty("city-place-circles", "visibility", showPlaces ? "visible" : "none");
  }, [stops, cityRoads, cityConnectors, cityStops, cityPlaces, roadRoute, assignmentResult, demandStreets, showRoads, showRoadSpeed, showStops, showPlaces, showConnectors, showPassengerFlow, showStationLoads, showDemandStreets, showPopulation]);

  const network = useMemo(
    () => buildNetworkPayload(stops, mode, routeName, headways),
    [stops, mode, routeName, headways],
  );

  const routeRows = useMemo(
    () =>
      network.routes.map((route) => {
        const service = network.services.find((item) => item.route_id === route.id);
        const vehicle = network.vehicle_types.find((item) => item.id === service?.vehicle_type_id);
        return {
          route,
          service,
          vehicle,
        };
      }),
    [network],
  );

  const totalDailyDepartures = useMemo(
    () =>
      network.services.reduce((sum, service) => {
        const departures = Object.entries(service.headway_by_period).reduce(
          (periodSum, [periodId, value]) => {
            const period = network.periods.find((item) => item.id === periodId);
            if (!period || value <= 0) return periodSum;
            return periodSum + Math.ceil((period.end_minute - period.start_minute) / value);
          },
          0,
        );
        return sum + departures;
      }, 0),
    [network],
  );

  function commitStops(update: StopDraft[] | ((current: StopDraft[]) => StopDraft[])) {
    setStops((current) => {
      const next = typeof update === "function" ? update(current) : update;
      if (JSON.stringify(current) === JSON.stringify(next)) return current;
      setStopHistory((history) => [...history, current].slice(-30));
      setStopFuture([]);
      return next;
    });
  }

  function undoStops() {
    setStopHistory((history) => {
      if (history.length === 0) return history;
      const previous = history[history.length - 1];
      setStops((current) => {
        setStopFuture((future) => [...future, current].slice(-30));
        return previous;
      });
      return history.slice(0, -1);
    });
    setRoadRoute(null);
  }

  function redoStops() {
    setStopFuture((future) => {
      if (future.length === 0) return future;
      const next = future[future.length - 1];
      setStops((current) => {
        setStopHistory((history) => [...history, current].slice(-30));
        return next;
      });
      return future.slice(0, -1);
    });
    setRoadRoute(null);
  }
  function removeStop(stopId: string) {
    setRoadRoute(null);
    commitStops((current) => current.filter((stop) => stop.id !== stopId));
  }

  function clearRoute() {
    setRoadRoute(null);
    commitStops([]);
    setMessage("Маршрут очищен");
  }

  async function buildRoadRoute() {
    const map = mapRef.current;
    if (!map || stops.length < 2) return;
    setBusy(true);
    setMessage("Построение маршрута по Overture…");
    try {
      const bounds = map.getBounds();
      const data = await loadOvertureRoute(
        stops.map((stop) => ({ lon: stop.lon, lat: stop.lat })),
        bounds.getSouth(),
        bounds.getWest(),
        bounds.getNorth(),
        bounds.getEast(),
      );
      setRoadRoute({
        type: "FeatureCollection",
        features: [
          {
            type: "Feature",
            geometry: data.geometry,
            properties: data.properties,
          },
        ],
      });
      setMessage(
        `Маршрут Overture: ${(data.properties.length_m / 1000).toFixed(2)} км, ${data.properties.travel_time_min.toFixed(1)} мин`,
      );
    } catch (error) {
      setRoadRoute(null);
      setMessage(error instanceof Error ? error.message : "Ошибка построения маршрута");
    } finally {
      setBusy(false);
    }
  }

  async function loadCityData() {
    const map = mapRef.current;
    if (!map) return;
    setBusy(true);
    setMessage("Загрузка Overture для текущей области…");
    try {
      const bounds = map.getBounds();
      const data = await loadOvertureNetwork(
        bounds.getSouth(),
        bounds.getWest(),
        bounds.getNorth(),
        bounds.getEast(),
      );
      setCityRoads(data.roads);
      setCityConnectors(data.connectors);
      setCityStops(data.stops);
      setCityPlaces(data.places);
      try {
        const population = await loadPopulationZones(
          bounds.getSouth(),
          bounds.getWest(),
          bounds.getNorth(),
          bounds.getEast(),
        );
        setPopulationZones(population);
      } catch {
        setPopulationZones(null);
      }
      setMessage(
        `Overture ${data.release}: ${data.counts.roads} участков, ${data.counts.connectors} коннекторов, ${data.counts.stops} остановок`,
      );
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Ошибка загрузки Overture");
    } finally {
      setBusy(false);
    }
  }

  async function validate() {
    setBusy(true);
    setMessage("Проверка сети…");
    try {
      const result = await validateNetwork(network);
      setMessage(
        result.valid
          ? "Сеть корректна"
          : `Ошибки: ${result.errors.join("; ")}`,
      );
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Ошибка проверки");
    } finally {
      setBusy(false);
    }
  }




  async function runCityAssignment() {
    const map = mapRef.current;
    if (!map || stops.length < 2) return;
    setBusy(true);
    setMessage("Расчёт городской сети по WorldPop + Overture…");
    try {
      const bounds = map.getBounds();
      const result = await calculateCityAssignment(
        network,
        bounds.getSouth(), bounds.getWest(), bounds.getNorth(), bounds.getEast(),
        network.origin_lon ?? bounds.getCenter().lng,
        network.origin_lat ?? bounds.getCenter().lat,
        "am",
      );
      setAssignmentResult(result.assignment);
      setCityAssignmentMeta(result.data);
      setMessage(`Citywide: ${result.data.zones} зон, ${result.data.places} Places, ${result.data.od_pairs} OD-пар`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Городской расчёт недоступен");
    } finally {
      setBusy(false);
    }
  }
  async function runPreviewAssignment() {
    if (stops.length < 2) return;
    setBusy(true);
    setMessage("Расчёт проверочного пассажиропотока…");
    try {
      const result = await calculateAssignment(
        network,
        [{ origin_zone_id: stops[0].id, destination_zone_id: stops[stops.length - 1].id, trips_per_day: previewTrips, purpose: "all" }],
        stops.map((stop) => ({ id: stop.id, centroid_x: toLocalMeters(stop.lon, stop.lat, stops[0].lon, stops[0].lat).x, centroid_y: toLocalMeters(stop.lon, stop.lat, stops[0].lon, stops[0].lat).y })),
        "am",
      );
      setAssignmentResult(result);
      const zones = stops.map((stop) => {
        const point = toLocalMeters(stop.lon, stop.lat, stops[0].lon, stops[0].lat);
        return { id: stop.id, centroid_x: point.x, centroid_y: point.y };
      });
      const streets = await loadDemandStreets(
        [{ origin_zone_id: stops[0].id, destination_zone_id: stops[stops.length - 1].id, trips_per_day: previewTrips, purpose: "all" }],
        zones,
        stops[0].lon,
        stops[0].lat,
      );
      setDemandStreets(streets);
      setMessage(`Пассажиропоток рассчитан: transit ${(result.metrics.transit_share * 100).toFixed(1)}%`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Ошибка расчёта пассажиропотока");
    } finally {
      setBusy(false);
    }
  }
  async function generateTimetable() {
    const service = network.services[0];
    if (!service) return;
    setBusy(true);
    setMessage("Формирование расписания…");
    try {
      const result = await createTimetable(service.id, network.periods, service.headway_by_period);
      setTimetable(result);
      setMessage("Расписание сформировано");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Ошибка формирования расписания");
    } finally {
      setBusy(false);
    }
  }
  function exportJson() {
    const project = {
      format: "transit-planner-project",
      version: 1,
      routeName,
      mode,
      headways,
      stops,
      network,
    };
    const blob = new Blob([JSON.stringify(project, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "transit-network.json";
    anchor.click();
    URL.revokeObjectURL(url);
    setMessage("JSON сети экспортирован");
  }


  function loadJsonFile(file: File) {
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const project = JSON.parse(String(reader.result));
        if (project.format !== "transit-planner-project") throw new Error("Неверный формат проекта");
        setRouteName(String(project.routeName ?? "Новый маршрут"));
        setMode((project.mode ?? "bus") as TransitMode);
        setHeadways({ ...headways, ...(project.headways ?? {}) });
        commitStops(Array.isArray(project.stops) ? project.stops : []);
        setRoadRoute(null);
        setTimetable(null);
        setMessage("Проект загружен");
      } catch (error) {
        setMessage(error instanceof Error ? error.message : "Не удалось загрузить проект");
      }
    };
    reader.readAsText(file);
  }
  return (
    <div className="app-shell">
      <header className="topbar">
        <div>
          <div className="brand">Transit Planner</div>
          <div className="subtitle">Проектирование транспортной сети</div>
        </div>
        <div className="actions">
          <button onClick={() => setViewMode("map")} className={viewMode === "map" ? "active-toggle" : ""}>
            Карта
          </button>
          <button onClick={() => setViewMode("network")} className={viewMode === "network" ? "active-toggle" : ""}>
            Network View
          </button>
          <button onClick={loadCityData} disabled={busy}>
            Загрузить Overture
          </button>
          <button onClick={buildRoadRoute} disabled={busy || stops.length < 2}>
            Построить по дорогам
          </button>
          <button
            className={drawMode ? "primary active" : "primary"}
            onClick={() => setDrawMode((value) => !value)}
          >
            {drawMode ? "Завершить рисование" : "Добавить остановки"}
          </button>
          <button onClick={validate} disabled={busy || stops.length < 2}>
            Проверить сеть
          </button>
          <button onClick={exportJson}>Сохранить</button><button onClick={() => loadInputRef.current?.click()}>Открыть</button><button onClick={undoStops} disabled={stopHistory.length === 0}>↶</button><button onClick={redoStops} disabled={stopFuture.length === 0}>↷</button><input ref={loadInputRef} type="file" accept="application/json" hidden onChange={(event) => { const file = event.target.files?.[0]; if (file) loadJsonFile(file); event.currentTarget.value = ""; }} />
        </div>
      </header>

      <div className="workspace">
        <aside className="sidebar">
          <section>
            <div className="section-title">Маршрут</div>
            <label>
              Название
              <input
                value={routeName}
                onChange={(event) => setRouteName(event.target.value)}
              />
            </label>
            <label>
              Вид транспорта
              <select
                value={mode}
                onChange={(event) => setMode(event.target.value as TransitMode)}
              >
                {Object.entries(MODE_LABELS).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <div className="preview-demand">
              <div className="section-title">Проверочный расчёт</div>
              <label>
                Спрос от первой к последней остановке, поездок/сутки
                <input
                  type="number"
                  min={1}
                  max={100000}
                  value={previewTrips}
                  onChange={(event) => setPreviewTrips(Number(event.target.value))}
                />
              </label>
              <button className="primary" onClick={runPreviewAssignment} disabled={busy || stops.length < 2}>
                Рассчитать пассажиропоток
              </button>
              <button onClick={runCityAssignment} disabled={busy || stops.length < 2}>
                Рассчитать городскую сеть
              </button>
            </div>

            <div className="period-headways">
              <div className="section-title">Частота по периодам</div>
              {network.periods.map((period) => (
                <label key={period.id}>
                  {period.id.replace("_", " ")}
                  <input
                    aria-label={period.id}
                    type="number"
                    min={1}
                    max={180}
                    value={headways[period.id] ?? 10}
                    onChange={(event) => setHeadways((current) => ({ ...current, [period.id]: Number(event.target.value) }))}
                  />
                </label>
              ))}
            </div>
          </section>

          <section className="stops-panel">
            <div className="section-title">
              Остановки <span>{stops.length}</span>
            </div>
            {stops.length === 0 ? (
              <div className="empty">
                Включите «Добавить остановки» и кликайте по карте.
              </div>
            ) : (
              <div className="stop-list">
                {stops.map((stop, index) => (
                  <div className="stop-row" key={stop.id}>
                    <div className="stop-number">{index + 1}</div>
                    <div className="stop-copy">
                      <strong>{stop.name}</strong>
                      <small>
                        {stop.lat.toFixed(5)}, {stop.lon.toFixed(5)}
                      </small>
                    </div>
                    <button
                      className="icon-button"
                      onClick={() => removeStop(stop.id)}
                      aria-label={`Удалить ${stop.name}`}
                    >
                      ×
                    </button>
                  </div>
                ))}
              </div>
            )}
          </section>

          <section className="layers-panel">
            <div className="section-title">Слои карты</div>
            <label className="check-row">
              <input type="checkbox" checked={showRoads} onChange={(event) => setShowRoads(event.target.checked)} />
              <span>Дороги Overture</span>
            </label>
            <label className="check-row">
              <input type="checkbox" checked={showRoadSpeed} onChange={(event) => setShowRoadSpeed(event.target.checked)} />
              <span>Скорость дорог</span>
            </label>
            <label className="check-row">
              <input type="checkbox" checked={showStops} onChange={(event) => setShowStops(event.target.checked)} />
              <span>Остановки</span>
            </label>
            <label className="check-row">
              <input type="checkbox" checked={showPlaces} onChange={(event) => setShowPlaces(event.target.checked)} />
              <span>Places</span>
            </label>
            <label className="check-row">
              <input type="checkbox" checked={showConnectors} onChange={(event) => setShowConnectors(event.target.checked)} />
              <span>Коннекторы</span>
            </label>
            <label className="check-row">
              <input type="checkbox" checked={showPassengerFlow} onChange={(event) => setShowPassengerFlow(event.target.checked)} />
              <span>Пассажиропоток</span>
            </label>
            <label className="check-row">
              <input type="checkbox" checked={showStationLoads} onChange={(event) => setShowStationLoads(event.target.checked)} />
              <span>Загрузка остановок</span>
            </label>
            <label className="check-row">
              <input type="checkbox" checked={showDemandStreets} onChange={(event) => setShowDemandStreets(event.target.checked)} />
              <span>Demand streets</span>
            </label>
            <label className="check-row">
              <input type="checkbox" checked={showPopulation} onChange={(event) => setShowPopulation(event.target.checked)} />
              <span>Население WorldPop</span>
            </label>
          </section>

          <section className="summary">
            <div className="section-title">Сводка</div>
            <div className="metric">
              <span>Остановок</span>
              <b>{stops.length}</b>
            </div>
            <div className="metric">
              <span>Интервал</span>
              <b>{headways.am} мин пик</b>
            </div>
            <div className="metric">
              <span>Вместимость</span>
              <b>{MODE_CAPACITY[mode]} мест</b>
            </div>
            <div className="metric">
              <span>Overture-дороги</span>
              <b>{cityRoads?.features.length ?? 0}</b>
            </div>
            <div className="metric">
              <span>Коннекторы</span>
              <b>{cityConnectors?.features.length ?? 0}</b>
            </div>
            <div className="metric">
              <span>Места</span>
              <b>{cityPlaces?.features.length ?? 0}</b>
            </div>
            <div className="metric">
              <span>Проверочный спрос</span>
              <b>{assignmentResult ? `${previewTrips}/сутки` : "—"}</b>
            </div>
            {assignmentResult && (
              <>
                <div className="metric">
                  <span>Transit share</span>
                  <b>{(assignmentResult.metrics.transit_share * 100).toFixed(1)}%</b>
                </div>
                {cityAssignmentMeta && (
                  <>
                    <div className="metric"><span>Зоны</span><b>{cityAssignmentMeta.zones}</b></div>
                    <div className="metric"><span>Places</span><b>{cityAssignmentMeta.places}</b></div>
                    <div className="metric"><span>OD-пары</span><b>{cityAssignmentMeta.od_pairs}</b></div>
                  </>
                )}
                <div className="metric">
                  <span>Макс. загрузка</span>
                  <b>{(assignmentResult.max_load_ratio * 100).toFixed(0)}%</b>
                </div>
              </>
            )}
          </section>

          <footer>{message}</footer>
        </aside>

        <main className={viewMode === "map" ? (drawMode ? "map-area drawing" : "map-area") : "network-area"}>
          {viewMode === "map" ? (
            <>
              <div ref={mapContainer} className="map" />
              {drawMode && (
                <div className="map-hint">
                  Кликайте по карте, чтобы добавлять остановки
                </div>
              )}
              <button className="clear-button" onClick={clearRoute}>
                Очистить
              </button>
            </>
          ) : (
            <div className="network-view">
              <div className="network-header">
                <div>
                  <h2>Network View</h2>
                  <p>Текущая схема линий, периодов и частоты обслуживания</p><button onClick={generateTimetable} disabled={busy || network.services.length === 0}>Сформировать расписание</button>
                </div>
                <div className="network-kpis">
                  <div><span>Линий</span><b>{network.routes.length}</b></div>
                  <div><span>Отправлений/сутки</span><b>{totalDailyDepartures}</b></div>
                  <div><span>Остановок</span><b>{network.stops.length}</b></div>
                </div>
              </div>
              {routeRows.length === 0 ? (
                <div className="network-empty">
                  Добавьте минимум две остановки и создайте маршрут — он появится здесь.
                </div>
              ) : (
                <div className="network-table-wrap">
                  <table className="network-table">
                    <thead>
                      <tr>
                        <th>Линия</th>
                        <th>Режим</th>
                        <th>Остановки</th>
                        <th>Вместимость</th>
                        {network.periods.map((period) => <th key={period.id}>{period.id}</th>)}
                      </tr>
                    </thead>
                    <tbody>
                      {routeRows.map(({ route, service, vehicle }) => (
                        <tr key={route.id}>
                          <td><strong>{route.name}</strong></td>
                          <td>{MODE_LABELS[route.mode]}</td>
                          <td>{route.stop_ids.length}</td>
                          <td>{vehicle?.capacity ?? "—"}</td>
                          {network.periods.map((period) => (
                            <td key={period.id}>
                              {service?.headway_by_period[period.id]
                                ? `${service.headway_by_period[period.id]} мин`
                                : "—"}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {assignmentResult && (
                <div className="analytics-panel">
                  <div className="section-title">Результат расчёта</div>
                  <div className="analytics-grid">
                    <div><span>Transit</span><b>{assignmentResult.metrics.transit_trips.toFixed(1)}</b></div>
                    <div><span>Car</span><b>{assignmentResult.metrics.car_trips.toFixed(1)}</b></div>
                    <div><span>Walk</span><b>{assignmentResult.metrics.walk_trips.toFixed(1)}</b></div>
                    <div><span>Bike</span><b>{assignmentResult.metrics.bike_trips.toFixed(1)}</b></div>
                  </div>
                  {assignmentResult.loss_reasons.length > 0 && (
                    <div className="loss-list">
                      <div className="small-label">Причины неперехода в transit</div>
                      {assignmentResult.loss_reasons.map((loss) => (
                        <div className="loss-row" key={loss.reason}>
                          <span>{loss.reason}</span>
                          <b>{loss.trips.toFixed(1)}</b>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {timetable && (
                <div className="timetable-panel">
                  <div className="section-title">Отправления сервиса {timetable.service_id}</div>
                  {timetable.periods.map((period) => (
                    <div className="timetable-row" key={period.period_id}>
                      <strong>{period.period_id}</strong>
                      <span>{period.departures_minute.slice(0, 8).map((minute) => {
                        const hours = Math.floor(minute / 60).toString().padStart(2, "0");
                        const mins = Math.round(minute % 60).toString().padStart(2, "0");
                        return `${hours}:${mins}`;
                      }).join(", ")}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
