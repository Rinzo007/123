import { useEffect, useMemo, useRef, useState } from "react";
import maplibregl, { type GeoJSONSource, type Map as MapLibreMap } from "maplibre-gl";
import type { Feature, FeatureCollection, Point as GeoJSONPoint, LineString } from "geojson";
import { validateNetwork } from "./api";
import type { NetworkPayload, StopDraft, TransitMode } from "./types";

const DEFAULT_CENTER: [number, number] = [39.20, 51.67];
const MAP_STYLE =
  import.meta.env.VITE_MAP_STYLE_URL ??
  "https://demotiles.maplibre.org/style.json";

const MODE_LABELS: Record<TransitMode, string> = {
  bus: "Автобус",
  trolleybus: "Троллейбус",
  tram: "Трамвай",
  metro: "Метро",
  regional_rail: "Железная дорога",
};

const MODE_CAPACITY: Record<TransitMode, number> = {
  bus: 80,
  trolleybus: 80,
  tram: 200,
  metro: 600,
  regional_rail: 800,
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
  headway: number,
): NetworkPayload {
  const origin = stops[0] ?? {
    lon: DEFAULT_CENTER[0],
    lat: DEFAULT_CENTER[1],
  };

  const metricStops = stops.map((stop) => {
    const location = toLocalMeters(
      stop.lon,
      stop.lat,
      origin.lon,
      origin.lat,
    );
    return {
      id: stop.id,
      name: stop.name,
      location,
      is_station: false,
    };
  });

  const geometryPoints = metricStops.map((stop) => stop.location);
  const route = {
    id: "draft-route",
    name: routeName,
    mode,
    stop_ids: stops.map((stop) => stop.id),
    geometry:
      geometryPoints.length >= 2
        ? { points: geometryPoints }
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
    stops: metricStops,
    routes: stops.length >= 2 ? [route] : [],
    vehicle_types: [vehicleType],
    periods: [{ id: "peak", start_minute: 360, end_minute: 540 }],
    services:
      stops.length >= 2
        ? [
            {
              id: "draft-service",
              route_id: "draft-route",
              vehicle_type_id: vehicleType.id,
              headway_by_period: { peak: headway },
            },
          ]
        : [],
  };
}

function stopsGeoJSON(stops: StopDraft[]): FeatureCollection<
  GeoJSONPoint,
  { id: string; name: string }
> {
  return {
    type: "FeatureCollection",
    features: stops.map((stop): Feature<GeoJSONPoint, { id: string; name: string }> => ({
      type: "Feature",
      geometry: { type: "Point", coordinates: [stop.lon, stop.lat] },
      properties: { id: stop.id, name: stop.name },
    })),
  };
}

function routeGeoJSON(stops: StopDraft[]): FeatureCollection<LineString, object> {
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
  const drawModeRef = useRef(false);
  const [drawMode, setDrawMode] = useState(false);
  const [stops, setStops] = useState<StopDraft[]>([]);
  const [mode, setMode] = useState<TransitMode>("bus");
  const [routeName, setRouteName] = useState("Новый маршрут");
  const [headway, setHeadway] = useState(10);
  const [message, setMessage] = useState("Готово к редактированию");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    drawModeRef.current = drawMode;
  }, [drawMode]);

  useEffect(() => {
    if (!mapContainer.current) return;

    const map = new maplibregl.Map({
      container: mapContainer.current,
      style: MAP_STYLE,
      center: DEFAULT_CENTER,
      zoom: 11,
      attributionControl: true,
    });

    mapRef.current = map;
    map.addControl(new maplibregl.NavigationControl(), "top-right");

    const handleMapClick = (event: maplibregl.MapMouseEvent) => {
      if (!drawModeRef.current) return;

      const id = `stop-${Date.now()}`;
      setStops((current) => [
        ...current,
        {
          id,
          name: `Остановка ${current.length + 1}`,
          lon: event.lngLat.lng,
          lat: event.lngLat.lat,
        },
      ]);
    };

    const handleLoad = () => {
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
          "circle-stroke-width": 2,
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
    const stopSource = map.getSource("draft-stops") as GeoJSONSource | undefined;

    routeSource?.setData(routeGeoJSON(stops));
    stopSource?.setData(stopsGeoJSON(stops));
  }, [stops]);

  const network = useMemo(
    () => buildNetworkPayload(stops, mode, routeName, headway),
    [stops, mode, routeName, headway],
  );

  function removeStop(stopId: string) {
    setStops((current) => current.filter((stop) => stop.id !== stopId));
  }

  function clearRoute() {
    setStops([]);
    setMessage("Маршрут очищен");
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

  function exportJson() {
    const blob = new Blob([JSON.stringify(network, null, 2)], {
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

  return (
    <div className="app-shell">
      <header className="topbar">
        <div>
          <div className="brand">Transit Planner</div>
          <div className="subtitle">Проектирование транспортной сети</div>
        </div>
        <div className="actions">
          <button
            className={drawMode ? "primary active" : "primary"}
            onClick={() => setDrawMode((value) => !value)}
          >
            {drawMode ? "Завершить рисование" : "Добавить остановки"}
          </button>
          <button onClick={validate} disabled={busy || stops.length < 2}>
            Проверить сеть
          </button>
          <button onClick={exportJson}>Экспорт JSON</button>
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
            <label>
              Интервал, мин
              <input
                type="number"
                min={1}
                max={120}
                value={headway}
                onChange={(event) => setHeadway(Number(event.target.value))}
              />
            </label>
          </section>

          <section className="stops-panel">
            <div className="section-title">
              Остановки <span>{stops.length}</span>
            </div>
            {stops.length === 0 ? (
              <div className="empty">Включите «Добавить остановки» и кликайте по карте.</div>
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

          <section className="summary">
            <div className="section-title">Сводка</div>
            <div className="metric">
              <span>Остановок</span>
              <b>{stops.length}</b>
            </div>
            <div className="metric">
              <span>Интервал</span>
              <b>{headway} мин</b>
            </div>
            <div className="metric">
              <span>Вместимость</span>
              <b>{MODE_CAPACITY[mode]} мест</b>
            </div>
          </section>

          <footer>{message}</footer>
        </aside>

        <main className={drawMode ? "map-area drawing" : "map-area"}>
          <div ref={mapContainer} className="map" />
          {drawMode && (
            <div className="map-hint">
              Кликайте по карте, чтобы добавлять остановки
            </div>
          )}
          <button className="clear-button" onClick={clearRoute}>
            Очистить
          </button>
        </main>
      </div>
    </div>
  );
}
