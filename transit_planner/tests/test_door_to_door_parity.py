"""Паритет door-to-door маршрутов между TS и routing.py.

Python здесь эталон. Проверка собирает настоящий journey.ts локальным tsc,
прогоняет его через настоящий routing.worker.ts и сверяет результат с
TransitRouter.shortest_from_points на той же фикстуре.

Допуск по длительностям — 5e-3 мин (~1.5 см ходьбы): TS хранит уличный граф
в микроградусах WGS84, поэтому его метры квантованы. Фикстура держится вдали
от границ кэтчмента, чтобы квантование не влияло на выбор остановок.
Если node или typescript недоступны, тест пропускается.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from math import cos, pi, radians
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from transit_planner.geo import Point  # noqa: E402
from transit_planner.network import (  # noqa: E402
    Network,
    Route,
    Service,
    ServicePeriod,
    Stop,
    TransitMode,
    VehicleType,
)
from transit_planner.road import RoadEdge, RoadGraph, RoadNode  # noqa: E402
from transit_planner.routing import TransitRouter  # noqa: E402

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
SRC = FRONTEND / "src"
TSC_JS = FRONTEND / "node_modules" / "typescript" / "lib" / "tsc.js"

ORIGIN_LON = 39.2
ORIGIN_LAT = 51.67
EARTH_RADIUS_M = 6_378_137.0
DURATION_TOLERANCE_MIN = 5e-3

# Остановки в метрах; точка запроса ровно между a и b, но посадка на b
# экономит транзитный сегмент — проверяет поиск по всем остановкам радиуса.
STOPS = (("a", 0.0), ("b", 600.0), ("c", 1200.0))
QUERY_X = 300.0

PROBE = """
import { planJourney } from "./journey.js";

const outbox = [];
globalThis.self = {
  postMessage: (message) => outbox.push(message),
  onmessage: null,
};
await import("./workers/routing.worker.js");
const handler = globalThis.self.onmessage;
class RealWorker {
  onmessage = null;
  onerror = null;
  postMessage(message) {
    handler({ data: message });
    const reply = outbox.pop();
    queueMicrotask(() => this.onmessage?.({ data: reply }));
  }
  terminate() {}
}
globalThis.Worker = RealWorker;

const spec = JSON.parse(process.argv[2]);
const asInt = (values) => Int32Array.from(values);
const asByte = (values) => Uint8Array.from(values);
const graph = {
  vertexCount: spec.graph.nodes.length,
  edgeCount: spec.graph.edges.length,
  lon: asInt(spec.graph.nodes.map(([lon]) => lon)),
  lat: asInt(spec.graph.nodes.map(([, lat]) => lat)),
  component: asInt(spec.graph.nodes.map(() => 0)),
  edgeA: asInt(spec.graph.edges.map(([a]) => a)),
  edgeB: asInt(spec.graph.edges.map(([, b]) => b)),
  edgeLengthM: asInt(spec.graph.edges.map(([, , length]) => length)),
  edgeClass: asByte(spec.graph.edges.map(() => 0)),
  edgeSpeedKph: asByte(spec.graph.edges.map(() => 30)),
  edgeDirection: asByte(spec.graph.edges.map(() => 0)),
  geomOffset: asInt(spec.graph.geomOffset),
  geomDeltaX: asInt(spec.graph.geomDelta[0]),
  geomDeltaY: asInt(spec.graph.geomDelta[1]),
  names: [],
};
const plan = await planJourney({
  network: spec.network,
  streetGraph: graph,
  periodId: "am",
  origin: spec.origin,
  destination: spec.destination,
  revision: 1,
  departureMin: 420,
});
process.stdout.write(JSON.stringify({
  found: plan.found,
  originStopId: plan.originStopId,
  destinationStopId: plan.destinationStopId,
  legs: plan.legs.map((leg) => ({
    mode: leg.mode,
    fromStopId: leg.fromStopId,
    toStopId: leg.toStopId,
    durationMin: leg.arriveMin - leg.departMin,
  })),
  walkToMin: plan.walkToMin,
  walkFromMin: plan.walkFromMin,
}));
"""


def _wgs84(x: float, y: float) -> tuple[float, float]:
    cos_lat = cos(radians(ORIGIN_LAT))
    lon = ORIGIN_LON + (x / (EARTH_RADIUS_M * cos_lat)) * 180.0 / pi
    lat = ORIGIN_LAT + (y / EARTH_RADIUS_M) * 180.0 / pi
    return lon, lat


def _micro(value: float) -> int:
    return round(value * 1_000_000)


def _fixture() -> tuple[Network, RoadGraph, Point, Point]:
    network = Network()
    for stop_id, x in STOPS:
        network.add_stop(Stop(stop_id, stop_id.upper(), Point(x, 0.0)))
    network.add_vehicle_type(VehicleType("bus", "Bus", TransitMode.BUS, 90))
    network.add_period(ServicePeriod("am", 360, 540))
    network.add_route(Route("r1", "1", TransitMode.BUS, ("a", "b", "c")))
    network.add_service(Service("svc", "r1", "bus", {"am": 10}))

    graph = RoadGraph()
    nodes = {"a": 0, "b": 1, "c": 2, "q": 3}
    positions = {"a": 0.0, "b": 600.0, "c": 1200.0, "q": QUERY_X}
    for name, node_id in nodes.items():
        graph.add_node(RoadNode(node_id, positions[name], 0.0))
    graph.add_edge(RoadEdge("qa", 3, 0, 300.0, 30.0, "residential"))
    graph.add_edge(RoadEdge("qb", 3, 1, 300.0, 30.0, "residential"))
    graph.add_edge(RoadEdge("bc", 1, 2, 600.0, 30.0, "residential"))
    return network, graph, Point(QUERY_X, 0.0), Point(1200.0, 0.0)


def _python_answer() -> dict:
    network, graph, origin, destination = _fixture()
    router = TransitRouter(network, road_graph=graph)
    journey = router.shortest_from_points(
        origin,
        destination,
        origin_id="door",
        destination_id="hall",
        period_id="am",
    )
    assert journey is not None
    return {
        "found": True,
        "originStopId": journey.origin_stop_id,
        "destinationStopId": journey.destination_stop_id,
        "legs": [
            {
                "mode": "walk" if leg.kind in ("access", "egress") else leg.kind,
                "fromStopId": leg.from_id,
                "toStopId": leg.to_id,
                "durationMin": leg.duration_min,
            }
            for leg in journey.legs
        ],
    }


def _ts_spec() -> dict:
    network, _, origin, destination = _fixture()
    nodes = []
    for stop_id, x in STOPS:
        nodes.append([_micro(_wgs84(x, 0.0)[0]), _micro(_wgs84(x, 0.0)[1])])
    nodes.append([_micro(_wgs84(QUERY_X, 0.0)[0]), _micro(_wgs84(QUERY_X, 0.0)[1])])
    edges = [(3, 0, 300), (3, 1, 300), (1, 2, 600)]
    geom_offset = []
    delta_x: list[int] = []
    delta_y: list[int] = []
    for a, b, _ in edges:
        geom_offset.append(len(delta_x))
        delta_x.append(nodes[b][0] - nodes[a][0])
        delta_y.append(nodes[b][1] - nodes[a][1])
    geom_offset.append(len(delta_x))
    origin_lon, origin_lat = _wgs84(origin.x, origin.y)
    dest_lon, dest_lat = _wgs84(destination.x, destination.y)
    return {
        "network": {
            "origin_lon": ORIGIN_LON,
            "origin_lat": ORIGIN_LAT,
            "stops": [
                {
                    "id": stop_id,
                    "name": stop_id.upper(),
                    "location": {"x": x, "y": 0.0},
                    "is_station": False,
                }
                for stop_id, x in STOPS
            ],
            "routes": [
                {
                    "id": "r1",
                    "name": "1",
                    "mode": "bus",
                    "stop_ids": ["a", "b", "c"],
                    "geometry": None,
                    "both_ways": True,
                    "closed": False,
                }
            ],
            "vehicle_types": [],
            "services": [
                {
                    "id": "svc",
                    "route_id": "r1",
                    "headway_by_period": {"am": 10},
                    "departure_offset_by_period": {"am": 0},
                }
            ],
            "periods": [{"id": "am", "start_minute": 360, "end_minute": 540}],
        },
        "graph": {
            "nodes": nodes,
            "edges": edges,
            "geomOffset": geom_offset,
            "geomDelta": [delta_x, delta_y],
        },
        "origin": {"lon": origin_lon, "lat": origin_lat},
        "destination": {"lon": dest_lon, "lat": dest_lat},
    }


# tsc сохраняет бессуффиксные импорты (`from "./projection"`), которые node ESM
# не разрешает. Дописываем `.js` к относительным спецификаторам без расширения
# во всех emitted-файлах — детерминированно и только средствами typescript.
_IMPORT_RE = re.compile(
    r"(?P<prefix>(?:from\s+|import\s*\())(?P<quote>[\"'])(?P<spec>\.{1,2}/[^\"']+)(?P=quote)"
)


def _fix_emitted_specifiers(root: Path) -> None:
    def fix(match: re.Match[str]) -> str:
        spec = match.group("spec")
        if "." in spec.rsplit("/", 1)[-1]:
            return match.group(0)
        quote = match.group("quote")
        return f"{match.group('prefix')}{quote}{spec}.js{quote}"

    for path in root.rglob("*.js"):
        path.write_text(_IMPORT_RE.sub(fix, path.read_text(encoding="utf-8")), encoding="utf-8")


def _ts_answer(tmp_path: Path) -> dict | None:
    node = shutil.which("node")
    if node is None or not TSC_JS.exists():
        return None
    result = subprocess.run(
        [
            node,
            str(TSC_JS),
            str(SRC / "journey.ts"),
            str(SRC / "workers" / "routing.worker.ts"),
            "--outDir",
            str(tmp_path),
            "--rootDir",
            str(SRC),
            "--module",
            "esnext",
            "--target",
            "es2022",
            "--ignoreConfig",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=FRONTEND,
    )
    if result.returncode != 0:
        pytest.fail(f"tsc не собрал стенд:\n{result.stdout}\n{result.stderr}")
    _fix_emitted_specifiers(tmp_path)
    (tmp_path / "probe.mjs").write_text(PROBE, encoding="utf-8")
    run = subprocess.run(
        [node, str(tmp_path / "probe.mjs"), json.dumps(_ts_spec())],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=tmp_path,
    )
    if run.returncode != 0:
        pytest.fail(f"стенд не отработал:\n{run.stdout}\n{run.stderr}")
    return json.loads(run.stdout)


@pytest.mark.skipif(
    not TSC_JS.exists() or shutil.which("node") is None,
    reason="node или typescript не установлены: TS-паритет не проверяется",
)
def test_door_to_door_matches_python(tmp_path: Path) -> None:
    ts = _ts_answer(tmp_path)
    if ts is None:
        pytest.skip("tsc недоступен")
    expected = _python_answer()
    assert ts["found"] is True, "TS обязан найти тот же маршрут, что и эталон"
    assert ts["originStopId"] == expected["originStopId"] == "b"
    assert ts["destinationStopId"] == expected["destinationStopId"] == "c"
    # TS несёт конкретный режим (bus/tram/...), Python — род leg'а.
    ts_modes = ["walk" if mode == "walk" else "transit" for mode in (leg["mode"] for leg in ts["legs"])]
    assert ts_modes == ["walk", "transit", "walk"]
    # Метки query-точек различаются по построению (TS: origin/destination,
    # Python: door/hall); сверяются стыки с транзитом и длительности.
    assert ts["legs"][0]["toStopId"] == "b"
    assert ts["legs"][1]["fromStopId"] == "b"
    assert ts["legs"][1]["toStopId"] == "c"
    assert ts["legs"][2]["fromStopId"] == "c"
    for actual, reference in zip(ts["legs"], expected["legs"], strict=True):
        actual_kind = "walk" if actual["mode"] == "walk" else "transit"
        assert actual_kind == reference["mode"]
        assert abs(actual["durationMin"] - reference["durationMin"]) <= (
            DURATION_TOLERANCE_MIN
        ), f"нога {actual}: TS {actual['durationMin']} != Python {reference['durationMin']}"
    assert abs(ts["walkToMin"] - 3.6) <= DURATION_TOLERANCE_MIN
    assert abs(ts["walkFromMin"] - 0.0) <= DURATION_TOLERANCE_MIN
