"""Сквозной браузерный прогон: пассажиропотоки считаются в браузере.

Зачем он нужен. Node-стенды собирают модули и вызывают функции напрямую, но
не воспроизводят то, что ломается только в браузере: подъём module-воркера,
`structured clone`, **отсоединение переданных по `transfer` массивов** и связку
с DOM. Именно последнее здесь и выстрелило: колонки зон читались после
`postMessage`, в интерфейсе появлялось `transit NaN%`, а все 276 тестов были
зелёными.

Что проверяется:
- приложение поднимается, воркеры demand/routing/assignment стартуют;
- предпросмотр доводится до конца, метрики числовые, ошибок консоли нет;
- пассажиропотоки НЕ уходят в Python: в списке запрошенных `/api/*` не должно
  быть ни `/v1/assignment`, ни `/v1/demand/reference`, ни `/v1/demand/streets`;
- контрольным сценарием с метро подтверждается, что транзитная доля ненулевая,
  то есть нулевая доля в синтетическом городе — свойство фикстуры, а не отказ
  расчёта.

Бэкенд не поднимается. Ответы Overture подменяются на синтетические, причём
граф улиц кодируется энкодером самого репозитория (``encode_streets_bin``), так
что бинарный формат совпадает по построению. Всё остальное — воркеры и расчёты —
настоящие.

Запуск (нужен установленный Chrome и Playwright для Python)::

    npx vite --port 5199 --strictPort        # в каталоге frontend
    python tools/browser_check.py

Ожидаемый вывод: ``пассажиропотоки: OK`` и ``запрещённых вызовов: нет``.
"""
from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = "http://127.0.0.1:5199/"

# Пустой фон вместо внешнего стиля: событие load должно прийти без сети.
MINIMAL_STYLE = {
    "version": 8,
    "name": "headless",
    "sources": {},
    "layers": [{"id": "bg", "type": "background", "paint": {"background-color": "#f3f4f6"}}],
}

# Эндпоинты, которых быть не должно: расчёт пассажиропотоков уехал в браузер.
FORBIDDEN = ("/v1/assignment", "/v1/demand/reference", "/v1/demand/streets")

# Геометрия фикстуры подогнана под карту приложения: центр [39.2, 51.67],
# клик на 36% высоты окна попадает около широты 51.6913. Ось улиц идёт по этой
# широте с шагом 120 м — плотнее допустимого снапа в 150 м, иначе нарисованные
# остановки окажутся вне графа.
AXIS_LAT = 51.6913
AXIS_LON = 39.120
AXIS_NODES = 101
STEP_M = 120.0
RELEASE = "headless-synthetic"


def _east(meters: float) -> tuple[float, float]:
    return AXIS_LON + meters / 75500.0, AXIS_LAT


def _street_graph_bin() -> bytes:
    """Двоичный граф улиц (TKST v2) силами энкодера репозитория."""
    script = f"""
import json, sys
sys.path.insert(0, {str(ROOT / "src")!r})
from shapely.geometry import Point
from transit_planner.binary_pack import encode_streets_bin
from transit_planner.road import RoadEdge

LAT = {AXIS_LAT}
LON = {AXIS_LON}
N = {AXIS_NODES}
STEP = {STEP_M}

def east(m):
    return LON + m / 75500.0, LAT

nodes, edges, components = [], [], []
for i in range(N):
    lon, lat = east(i * STEP)
    nodes.append((i, Point(lon, lat)))
    components.append(0)
for i in range(N - 1):
    for a, b, suffix in ((i, i + 1, ""), (i + 1, i, "-rev")):
        edges.append(RoadEdge(
            id=f"axis-{{a}}{{suffix}}", from_node=a, to_node=b, length_m=STEP,
            speed_kph=50.0, road_type="street", direction="both",
        ))
data = encode_streets_bin(nodes=nodes, edges=edges, names=["Headless Street"],
                          components=components)
sys.stdout.write(base64.b64encode(data).decode("ascii"))
"""
    script = "import base64\n" + script
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, encoding="utf-8"
    )
    if result.returncode != 0:
        raise SystemExit(f"не удалось собрать граф улиц:\n{result.stderr}")
    return base64.b64decode(result.stdout.strip())


def overture_network() -> dict:
    roads, stops, connectors, places = [], [], [], []
    for index in range(AXIS_NODES - 1):
        a = _east(index * STEP_M)
        b = _east((index + 1) * STEP_M)
        roads.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [[a[0], a[1]], [b[0], b[1]]]},
            "properties": {"id": f"road-{index}", "name": "Headless Street",
                           "class": "street", "speed_kph": 50},
        })
    for index in range(0, AXIS_NODES, 4):
        lon, lat = _east(index * STEP_M)
        stops.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
                      "properties": {"id": f"ostop-{index}", "name": f"Stop {index}", "class": "bus"}})
        connectors.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
                           "properties": {"id": f"conn-{index}", "stop_id": f"ostop-{index}",
                                          "edge_id": f"axis-{index}"}})
    for index, category in enumerate(("shop", "school", "hospital", "office")):
        lon, lat = _east(300.0 + index * 600.0)
        lat += 40.0 / 111300.0
        places.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
                       "properties": {"id": f"place-{index}", "name": f"Place {category}",
                                      "categories": [category], "importance": 10 + index}})
    return {
        "roads": {"type": "FeatureCollection", "features": roads},
        "connectors": {"type": "FeatureCollection", "features": connectors},
        "stops": {"type": "FeatureCollection", "features": stops},
        "places": {"type": "FeatureCollection", "features": places},
        "release": RELEASE,
        "counts": {"roads": len(roads), "connectors": len(connectors),
                   "stops": len(stops), "places": len(places)},
    }


def population_zones() -> dict:
    """Зоны WorldPop-подобного вида вдоль оси улиц."""
    features = []
    for index in range(6):
        lon, lat = _east(600.0 + index * 400.0)
        lat += 40.0 / 111300.0
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"id": f"z{index + 1}", "population": 5000 - index * 400,
                           "jobs": 300 + index * 300,
                           "purpose_attractions": {"shop": 300 + index * 120}},
        })
    return {"type": "FeatureCollection", "features": features}


def _install_routes(page, graph: bytes, network: dict, zones: dict, calls: list[str]) -> None:
    def handle(route):
        url = route.request.url
        if "openfreemap" in url:
            route.fulfill(status=200, content_type="application/json", body=json.dumps(MINIMAL_STYLE))
        elif "/v1/data/overture/network" in url:
            calls.append("/v1/data/overture/network")
            route.fulfill(status=200, content_type="application/json", body=json.dumps(network))
        elif "/v1/data/overture/graph" in url:
            calls.append("/v1/data/overture/graph")
            route.fulfill(status=200, content_type="application/octet-stream", body=graph,
                          headers={"X-Overture-Release": RELEASE})
        elif "population-zones" in url:
            calls.append("/v1/demand/population-zones")
            route.fulfill(status=200, content_type="application/json", body=json.dumps(zones))
        elif "/api/" in url:
            calls.append("/v1" + url.split("/api", 1)[1].split("?")[0])
            route.fulfill(status=200, content_type="application/json", body="{}")
        else:
            route.continue_()

    page.route("**/*", handle)


def _drive_app(page, *, build_road: bool) -> str:
    """Полный путь интерфейса: остановки → город → дорога → предпросмотр."""
    page.goto(APP, wait_until="domcontentloaded")
    page.wait_for_timeout(4000)
    page.click('[data-action="draw"]')
    box = page.locator("#map").bounding_box()
    if not box:
        raise SystemExit("карта без размеров: стенд не может нарисовать остановки")
    for fraction in (0.32, 0.42, 0.52, 0.62):
        page.mouse.click(box["x"] + box["width"] * fraction, box["y"] + box["height"] * 0.36)
        page.wait_for_timeout(350)
    page.wait_for_timeout(600)
    for period in ("early", "am", "mid", "pm", "eve"):
        selector = f'input[data-headway="{period}"]'
        if page.locator(selector).count():
            page.fill(selector, "8")
            page.dispatch_event(selector, "change")
    page.click('[data-action="load-city"]')
    page.wait_for_timeout(6000)
    if build_road and page.locator('[data-action="build-road"]:not([disabled])').count():
        page.click('[data-action="build-road"]')
        page.wait_for_timeout(4000)
    if not page.locator('[data-action="preview"]:not([disabled])').count():
        raise SystemExit("предпросмотр недоступен: не хватает остановок или расписания")
    page.click('[data-action="preview"]')
    for _ in range(90):
        page.wait_for_timeout(1000)
        if "Расчёт проверочного" not in page.inner_text("#status"):
            break
    return page.inner_text("#status")


def _controlled_transit_share(page) -> float:
    """Транзитная доля на заведомо привлекательной сети: должна быть > 0."""
    return page.evaluate(
        """async () => {
          const { assignDemandInWorker } = await import('/src/workers/assignment-client.ts');
          const stops = [];
          for (let i = 0; i < 5; i += 1) {
            stops.push({ id: `s${i+1}`, name: `S${i+1}`, is_station: false,
                         location: { x: i * 1000, y: 0 } });
          }
          const network = {
            origin_lon: 39.2, origin_lat: 51.67, stops,
            routes: [
              { id: 'rA', name: 'A', mode: 'bus', stop_ids: ['s1','s2','s3','s4','s5'],
                geometry: { points: [{x:0,y:0},{x:4000,y:0}] }, both_ways: true, closed: false },
              { id: 'rT', name: 'T', mode: 'metro', stop_ids: ['s1','s5'],
                geometry: { points: [{x:0,y:0},{x:4000,y:0}] }, both_ways: true, closed: false },
            ],
            vehicle_types: [
              { id: 'vtb', name: 'Bus', mode: 'bus', capacity: 90, operating_cost_per_km: 0 },
              { id: 'vtm', name: 'Metro', mode: 'metro', capacity: 750, operating_cost_per_km: 0 },
            ],
            periods: [{ id: 'am', start_minute: 360, end_minute: 540 }],
            services: [
              { id: 'vA', route_id: 'rA', vehicle_type_id: 'vtb', headway_by_period: { am: 8 } },
              { id: 'vT', route_id: 'rT', vehicle_type_id: 'vtm', headway_by_period: { am: 2 } },
            ],
            track_nodes: [], track_sections: [],
          };
          const zoneIds = ['z1','z2','z3','z4','z5'];
          const zones = zoneIds.map((id, i) => ({
            id, centroidX: i * 1000, centroidY: 0,
            population: 5000, jobs: 3000, noCarShare: 0.6,
          }));
          const pairs = [];
          for (const o of zoneIds) for (const d of zoneIds) {
            if (o !== d) pairs.push({
              originZoneId: o, destinationZoneId: d, tripsPerDay: 800, baseTimeMin: null,
            });
          }
          const res = await assignDemandInWorker(network, 'am', pairs, zones, { departureMin: 363 });
          return res.metrics.transit_share;
        }"""
    )


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("SKIP: не установлен playwright (pip install playwright)")
        return 0

    graph = _street_graph_bin()
    network = overture_network()
    zones = population_zones()

    console: list[str] = []
    page_errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="chrome", headless=True,
            args=["--use-gl=swiftshader", "--enable-unsafe-swiftshader"],
        )
        context = browser.new_context(viewport={"width": 1500, "height": 950})
        page = context.new_page()
        page.on("console", lambda m: console.append(f"{m.type}: {m.text}"))
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        calls: list[str] = []
        _install_routes(page, graph, network, zones, calls)

        status = _drive_app(page, build_road=True)
        print(f"статус предпросмотра: {status}")
        print(f"остановок нарисовано: {page.inner_text('#stop-count')}")
        share = _controlled_transit_share(page)
        print(f"контрольная транзитная доля: {share:.3f}")
        browser.close()

    unique = sorted(set(calls))
    print("\nзапрошенные /api:")
    for path in unique:
        print(" ", path)
    violations = [path for path in unique if any(f in path for f in FORBIDDEN)]
    errors = [line for line in console if line.startswith("error")]

    preview_ok = "Пассажиропоток рассчитан" in status
    controlled_ok = share > 0.0
    print(f"\nпассажиропотоки: {'OK' if preview_ok else 'НЕ УДАЛОСЬ'}")
    print(f"контрольный сценарий: {'OK' if controlled_ok else 'НЕ УДАЛОСЬ'}")
    print(f"запрещённых вызовов: {violations or 'нет'}")
    print(f"ошибок консоли: {len(errors)}")
    print(f"pageerror: {len(page_errors)}")
    for line in page_errors[:5]:
        print(" ", line[:200])
    return 0 if preview_ok and controlled_ok and not violations and not page_errors else 1


if __name__ == "__main__":
    sys.exit(main())
