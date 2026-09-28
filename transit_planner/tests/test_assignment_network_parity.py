"""Паритет network-слоя assignment: TS assignment-network.ts ↔ Python.

Python — эталон. Сеть кодируется в JSON ровно в той форме, которую принимает
frontend `NetworkPayload` (snake_case), и одновременно подаётся в эталонные
`_section_capacity_and_platforms`, `_stop_dwell_seconds`, `_nearest_stop_id` и
`_service_headway_feedback`.

Отдельно сравниваются: вместимости секций (включая разделение общей
физической шины между сервисами), платформы, стоянка, ближайшая остановка с
ограничением по access_m, и интервальные множители обратной связи.

Модуль тянет ./assignment-model и ./types (типы), поэтому стенд собирает их
вместе. Без node/typescript тест пропускается.
"""
from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from transit_planner.assignment import (  # noqa: E402
    _nearest_stop_id,
    _section_capacity_and_platforms,
    _service_headway_feedback,
    _stop_dwell_seconds,
)
from transit_planner.city import DemandZone  # noqa: E402
from transit_planner.network import (  # noqa: E402
    Network,
    Route,
    Service,
    ServicePeriod,
    Stop,
    TransitMode,
    TrackSection,
    VehicleType,
)
from transit_planner.assignment import _FlowSnapshot  # noqa: E402
from shapely.geometry import Point  # noqa: E402

SRC = ROOT / "frontend" / "src"
TSC_JS = ROOT / "frontend" / "node_modules" / "typescript" / "lib" / "tsc.js"
TOLERANCE = 1e-9

# --- Сеть -----------------------------------------------------------------
# r1 и r4 делят одну физическую шину s2—s3 (shared_group "corridor"), чтобы
# проверить распределение вместимости коридора по расписанию. r2 — трамвай без
# шины, у него своя предельная частота по профилю режима. r3 односторонний,
# чтобы проверить, что обратная секция не создаётся.
STOPS = [
    Stop(id="s1", name="S1", location=Point(0.0, 0.0), is_station=False),
    Stop(id="s2", name="S2", location=Point(600.0, 0.0), is_station=False),
    Stop(id="s3", name="S3", location=Point(1200.0, 0.0), is_station=False),
    Stop(id="s4", name="S4", location=Point(1800.0, 0.0), is_station=False),
    Stop(id="s5", name="S5", location=Point(2400.0, 0.0), is_station=False),
]
TRACKS = [
    TrackSection(
        id="t1", length_km=0.6, track_type="street",
        capacity_departures_per_hour=40, shared_group="corridor",
        station_ids=[], speed_limit_kph=None,
    ),
    # Шина с большей пропускной способностью, чем предел профиля трамвая (40
    # departures/hour): без этого сценарий не проверяет, что режимной предел
    # действительно ограничивает вместимость, а не висит мёртвым.
    TrackSection(
        id="t2", length_km=1.2, track_type="tram",
        capacity_departures_per_hour=200, shared_group=None,
        station_ids=[], speed_limit_kph=None,
    ),
]
ROUTES = [
    Route(id="r1", name="R1", mode=TransitMode.BUS, stop_ids=("s1", "s2", "s3"),
          both_ways=True, closed=False, track_section_ids=("t1", "t1")),
    Route(id="r2", name="R2", mode=TransitMode.TRAM, stop_ids=("s3", "s4"),
          both_ways=False, closed=False),
    Route(id="r3", name="R3", mode=TransitMode.BUS, stop_ids=("s1", "s5"),
          both_ways=False, closed=False),
    Route(id="r4", name="R4", mode=TransitMode.TRAM, stop_ids=("s1", "s5"),
          both_ways=False, closed=False, track_section_ids=("t2",)),
]
PERIODS = {
    "am": ServicePeriod(id="am", start_minute=360, end_minute=540),
    "pm": ServicePeriod(id="pm", start_minute=900, end_minute=1140),
}
SERVICES = [
    Service(id="v1", route_id="r1", vehicle_type_id="vt_bus", headway_by_period={"am": 12, "pm": 15}),
    Service(id="v2", route_id="r2", vehicle_type_id="vt_tram", headway_by_period={"am": 20}),
    Service(id="v3", route_id="r3", vehicle_type_id="vt_bus", headway_by_period={"am": 30, "pm": 30}),
    Service(id="v4", route_id="r4", vehicle_type_id="vt_tram", headway_by_period={"am": 2}),
]
VEHICLE_TYPES = [
    VehicleType(id="vt_bus", name="Bus", mode=TransitMode.BUS, capacity=90),
    VehicleType(id="vt_tram", name="Tram", mode=TransitMode.TRAM, capacity=250),
]
ZONES = [
    DemandZone("z1", 50.0, 0.0, population=1000.0, jobs=100.0, no_car_share=0.3),
    DemandZone("z2", 1150.0, 0.0, population=800.0, jobs=200.0, no_car_share=0.6),
    # Далеко за пределами access_m любого режима: остановки быть не должно.
    DemandZone("z_far", 9000.0, 0.0, population=100.0, jobs=10.0, no_car_share=0.5),
]
# Посадки по (service, stop) для расчёта интервальных множителей.
SERVICE_BOARDINGS = {
    ("v1", "s1"): 120.0, ("v1", "s2"): 90.0, ("v1", "s3"): 40.0,
    ("v2", "s3"): 15.0, ("v2", "s4"): 5.0,
    ("v3", "s1"): 7.0, ("v3", "s5"): 0.0,
}


def _build_network() -> Network:
    return Network(
        stops={s.id: s for s in STOPS},
        routes={r.id: r for r in ROUTES},
        vehicle_types={v.id: v for v in VEHICLE_TYPES},
        periods=PERIODS,
        services={s.id: s for s in SERVICES},
        track_sections={t.id: t for t in TRACKS},
    )


def _network_payload(period_id: str) -> dict:
    """Сеть в форме frontend NetworkPayload для стенда."""
    period = PERIODS[period_id]
    return {
        "origin_lon": 0.0,
        "origin_lat": 0.0,
        "stops": [
            {"id": s.id, "name": s.name, "is_station": s.is_station,
             "location": {"x": s.location.x, "y": s.location.y}}
            for s in STOPS
        ],
        "routes": [
            {
                "id": r.id, "name": r.name, "mode": r.mode.value,
                "stop_ids": list(r.stop_ids), "geometry": None,
                "track_section_ids": list(r.track_section_ids),
                "both_ways": r.both_ways, "closed": r.closed,
            }
            for r in ROUTES
        ],
        "vehicle_types": [
            {"id": v.id, "name": v.name, "mode": v.mode.value, "capacity": v.capacity,
             "operating_cost_per_km": v.operating_cost_per_km}
            for v in VEHICLE_TYPES
        ],
        "periods": [
            {"id": p.id, "start_minute": p.start_minute, "end_minute": p.end_minute}
            for p in (PERIODS["am"], PERIODS["pm"]) if p.id in (period_id, "am")
        ],
        "services": [
            {"id": s.id, "route_id": s.route_id, "vehicle_type_id": s.vehicle_type_id,
             "headway_by_period": dict(s.headway_by_period)}
            for s in SERVICES
            if period_id in s.headway_by_period
        ],
        "track_nodes": [],
        "track_sections": [
            {"id": t.id, "length_km": t.length_km, "track_type": t.track_type,
             "capacity_departures_per_hour": t.capacity_departures_per_hour,
             "shared_group": t.shared_group, "station_ids": list(t.station_ids),
             "speed_limit_kph": t.speed_limit_kph}
            for t in TRACKS
        ],
    }


PROBE = """
import { sectionCapacityAndPlatforms, stopDwellCoefficients, stopDwellFromCoefficients,
         nearestStopId, serviceHeadwayFactors, serviceDepartures, segmentPairs,
         segmentRunTimes } from "./assignment-network.js";

const spec = JSON.parse(process.argv[2]);
const network = spec.network;
const capacity = sectionCapacityAndPlatforms(network, spec.periodId);
const dwell = stopDwellCoefficients(network, spec.periodId);
const boardings = new Map(Object.entries(spec.boardings));
const boardingsByStop = new Map();
for (const [key, value] of boardings) {
  const sep = key.indexOf("|");
  boardingsByStop.set(key.slice(sep + 1), (boardingsByStop.get(key.slice(sep + 1)) ?? 0) + value);
}
const nearest = spec.zones.map((z) => nearestStopId(
  network, { centroidX: z.x, centroidY: z.y }, z.maxDistance, spec.periodId,
));
process.stdout.write(JSON.stringify({
  capacity: capacity.capacity
    .map((c) => [c.routeId, c.fromStopId, c.toStopId, c.capacity])
    .sort((a, b) => (a[0] === b[0] ? (a[1] === b[1] ? a[2].localeCompare(b[2]) : a[1].localeCompare(b[1])) : a[0].localeCompare(b[0]))),
  platform: [...capacity.platformM.entries()].sort(),
  dwellBase: [...dwell.base.entries()].sort(),
  dwellPerPassenger: [...dwell.perPassenger.entries()].sort(),
  dwellSample: spec.dwellSamples.map((s) => stopDwellFromCoefficients(dwell, s[0], s[1])),
  nearest,
  headway: [...serviceHeadwayFactors(network, spec.periodId, boardings).entries()].sort(),
  departures: network.services
    .map((s) => [s.id, serviceDepartures(network, s, spec.periodId)])
    .sort(),
  segmentPairs: network.routes.map((r) => [r.id, segmentPairs(r)]),
  runTimes: [...segmentRunTimes(network).entries()].sort(),
}));
"""


def _ts_out(tmp_path: Path, period_id: str) -> dict | None:
    node = shutil.which("node")
    if node is None or not TSC_JS.exists():
        return None
    build = subprocess.run(
        [
            node, str(TSC_JS), str(SRC / "assignment-network.ts"),
            str(SRC / "assignment-model.ts"), str(SRC / "choice.ts"), str(SRC / "types.ts"),
            "--outDir", str(tmp_path),
            "--rootDir", str(SRC),
            "--module", "esnext",
            "--target", "es2022",
            "--ignoreConfig",
        ],
        capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT / "frontend"),
    )
    if build.returncode != 0:
        pytest.fail(f"tsc не собрал стенд:\n{build.stdout}\n{build.stderr}")
    # tsc оставляет extensionless-импорты, а Node ESM их не разрешает.
    for name in ("assignment-network.js", "assignment-model.js"):
        compiled = tmp_path / name
        text = compiled.read_text(encoding="utf-8")
        for target in ("./assignment-model", "./choice", "./types"):
            text = text.replace(f'from "{target}"', f'from "{target}.js"')
        compiled.write_text(text, encoding="utf-8")
    (tmp_path / "probe.mjs").write_text(PROBE, encoding="utf-8")
    payload = {
        "network": _network_payload(period_id),
        "periodId": period_id,
        "boardings": {f"{a}|{b}": v for (a, b), v in SERVICE_BOARDINGS.items()},
        "zones": [
            {"x": z.centroid_x, "y": z.centroid_y, "maxDistance": 1500.0} for z in ZONES
        ],
        "dwellSamples": [[s.id, 42.0] for s in STOPS],
    }
    run = subprocess.run(
        [node, str(tmp_path / "probe.mjs"), json.dumps(payload)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(tmp_path),
    )
    if run.returncode != 0:
        pytest.fail(f"стенд network-слоя не отработал:\n{run.stdout}\n{run.stderr}")
    return json.loads(run.stdout)


def _python_out(period_id: str) -> dict:
    network = _build_network()
    capacity, platform = _section_capacity_and_platforms(network, period_id)
    snapshot = _FlowSnapshot(
        metrics=None,  # type: ignore[arg-type]
        route_flows=(), section_loads=(), stop_flows=(), unserved=0.0, loss_reasons=(),
        service_stop_boardings=tuple(
            (service, stop, value)
            for (service, stop), value in sorted(SERVICE_BOARDINGS.items())
        ),
    )
    return {
        "capacity": sorted((r, f, t, c) for r, f, t, c in capacity),
        "platform": sorted(platform.items()),
        "nearest": [
            _nearest_stop_id(network, zone, 1500.0, period_id) for zone in ZONES
        ],
        "headway": sorted(_service_headway_feedback(network, snapshot, period_id).items()),
        "departures": sorted(
            (s.id, network.service_departures(s, period_id))
            for s in SERVICES if period_id in s.headway_by_period
        ),
        "dwellSample": [
            _stop_dwell_seconds(network, s.id, period_id, 42.0) for s in STOPS
        ],
        "segmentPairs": sorted((r.id, list(r.segment_pairs())) for r in ROUTES),
    }


@pytest.mark.skipif(
    not TSC_JS.exists() or shutil.which("node") is None,
    reason="node или typescript не установлены: TS-паритет не проверяется",
)
@pytest.mark.parametrize("period_id", ["am", "pm"])
def test_section_capacity_matches_python(tmp_path: Path, period_id: str) -> None:
    ts = _ts_out(tmp_path, period_id)
    if ts is None:
        pytest.skip("tsc недоступен")
    expected = _python_out(period_id)

    assert len(ts["capacity"]) == len(expected["capacity"]), (
        f"{period_id}: число секций разошлось\nTS {ts['capacity']}\nPy {expected['capacity']}"
    )
    for left, right in zip(ts["capacity"], expected["capacity"], strict=True):
        assert left[:3] == list(right[:3]), f"{period_id}: секция {left[:3]} != {right[:3]}"
        assert abs(left[3] - right[3]) <= TOLERANCE * max(1.0, abs(right[3])), (
            f"{period_id}: вместимость {left[:3]}: TS {left[3]} != Python {right[3]}"
        )

    assert ts["platform"] == [list(row) for row in expected["platform"]], (
        f"{period_id}: длины платформ разошлись\nTS {ts['platform']}\nPy {expected['platform']}"
    )


@pytest.mark.skipif(
    not TSC_JS.exists() or shutil.which("node") is None,
    reason="node или typescript не установлены: TS-паритет не проверяется",
)
@pytest.mark.parametrize("period_id", ["am", "pm"])
def test_dwell_and_departures_match_python(tmp_path: Path, period_id: str) -> None:
    ts = _ts_out(tmp_path, period_id)
    if ts is None:
        pytest.skip("tsc недоступен")
    expected = _python_out(period_id)

    for left, right in zip(ts["dwellSample"], expected["dwellSample"], strict=True):
        assert abs(left - right) <= TOLERANCE * max(1.0, abs(right)), (
            f"{period_id}: dwell {left} != Python {right}"
        )
    for left, right in zip(ts["departures"], expected["departures"], strict=True):
        assert left[0] == right[0]
        assert left[1] == right[1], f"{period_id}: departures {left} != {right}"


@pytest.mark.skipif(
    not TSC_JS.exists() or shutil.which("node") is None,
    reason="node или typescript не установлены: TS-паритет не проверяется",
)
def test_nearest_stop_and_headway_factors_match_python(tmp_path: Path) -> None:
    ts = _ts_out(tmp_path, "am")
    if ts is None:
        pytest.skip("tsc недоступен")
    expected = _python_out("am")

    assert ts["nearest"] == expected["nearest"], (
        f"ближайшие остановки разошлись\nTS {ts['nearest']}\nPy {expected['nearest']}"
    )
    assert len(ts["headway"]) == len(expected["headway"])
    for left, right in zip(ts["headway"], expected["headway"], strict=True):
        assert left[0] == right[0]
        assert abs(left[1] - right[1]) <= TOLERANCE, (
            f"headway factor {left[0]}: TS {left[1]} != Python {right[1]}"
        )


@pytest.mark.skipif(
    not TSC_JS.exists() or shutil.which("node") is None,
    reason="node или typescript не установлены: TS-паритет не проверяется",
)
def test_segment_pairs_match_python(tmp_path: Path) -> None:
    ts = _ts_out(tmp_path, "am")
    if ts is None:
        pytest.skip("tsc недоступен")
    expected = _python_out("am")
    assert sorted(ts["segmentPairs"]) == sorted(
        [route_id, [list(pair) for pair in pairs]] for route_id, pairs in expected["segmentPairs"]
    )


def test_shared_corridor_capacity_is_split_not_doubled() -> None:
    """Общая шина не удваивает вместимость: предел один на весь коридор.

    r1 проходит по t1 дважды (s1→s2 и s2→s3), поэтому эталон делит лимит
    коридора между двумя секциями по доле расписания.
    """
    network = _build_network()
    capacity, _platform = _section_capacity_and_platforms(network, "am")
    per_section = {(r, f, t): c for r, f, t, c in capacity}
    corridor_departures = 40.0 * 3.0  # departures/hour * 3 часа
    half = corridor_departures / 2
    assert per_section[("r1", "s2", "s3")] == pytest.approx(half * 90.0)
    assert per_section[("r1", "s1", "s2")] == pytest.approx(half * 90.0)
    # Обратная секция появляется только у двустороннего r1 и равна прямой.
    assert per_section[("r1", "s3", "s2")] == pytest.approx(half * 90.0)
    # Односторонний r2 в шину не входит: 180/20 = 9 рейсов за период, профиль
    # трамвая (40 departures/hour * 3 часа = 120) не ограничивает.
    assert per_section[("r2", "s3", "s4")] == pytest.approx(9.0 * 250.0)
    assert ("r2", "s4", "s3") not in per_section
    # r4: шина тянет 200 departures/hour * 3 часа = 600, но профиль трамвая
    # ограничивает 40 * 3 = 120 рейсов — именно этот предел должен сработать.
    assert per_section[("r4", "s1", "s5")] == pytest.approx(120.0 * 250.0)


def test_nearest_stop_respects_mode_access_limit() -> None:
    """Остановка дальше access_m режима недоступна даже при большем радиусе."""
    network = _build_network()
    # z_far далеко от всех остановок — остановки нет ни при каком радиусе.
    assert _nearest_stop_id(network, ZONES[2], 1500.0, "am") is None
    # Ближайшая к x=480 остановка — s2 (600 м), она в пределах bus access_m.
    near = DemandZone("z", 480.0, 0.0, population=10.0, jobs=1.0, no_car_share=0.0)
    assert _nearest_stop_id(network, near, 1500.0, "am") == "s2"
    # Тот же пункт, но радиус меньше расстояния до s2 — остановки нет.
    assert _nearest_stop_id(network, near, 100.0, "am") is None
    # Радиус больше bus access_m (500 м) не помогает: зона в 2500 м от всех.
    assert _nearest_stop_id(network, ZONES[2], 100000.0, "am") is None


def test_headway_factor_grows_with_boardings() -> None:
    """Множитель интервала растёт с загрузкой: обратная связь знак верный."""
    network = _build_network()
    empty = _service_headway_feedback(
        network,
        _FlowSnapshot(None, (), (), (), 0.0, ()),  # type: ignore[arg-type]
        "am",
    )
    loaded = _service_headway_feedback(
        network,
        _FlowSnapshot(
            None, (), (), (), 0.0, (),  # type: ignore[arg-type]
            (("v1", "s1", 500.0), ("v1", "s2", 500.0), ("v1", "s3", 500.0)),
        ),
        "am",
    )
    assert loaded["v1"] > empty["v1"] >= 1.0
    # Сервис без интервала в периоде множителя не получает.
    assert "v2" in empty
    assert math.isclose(empty["v2"], 1.0 + min(1.0, 60.0 / (20.0 * 60.0)) ** 2)
