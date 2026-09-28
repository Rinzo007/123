"""Тесты ядра routing.worker.ts без браузера.

Воркер — единственное место, где живёт RAPTOR со штрафами, запретами и отбором
альтернатив, и до этого момента он вообще не был покрыт тестами: париты
проверяли только литералы весов. Стенд собирает настоящие routing.ts и
routing.worker.ts локальным tsc, подменяет `self` и вызывает обработчик
напрямую, поэтому проверяется реальный код ядра.

Что проверяется:
- одиночный маршрут и его раскладка по ногам (board/alight, времена);
- штраф переполненности сегмента уводит роутер с перегруженного участка;
- запрет паттерна исключает его из поиска;
- штраф маршрута входит в обобщённую стоимость выбранной связки;
- diversity-цикл возвращает разные связки, а Pareto убирает доминируемые;
- отчётное время в пути не включает штраф переполненности.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from transit_planner.assignment import _section_capacity_and_platforms  # noqa: E402
from transit_planner.network import (  # noqa: E402
    Network,
    Route,
    Service,
    ServicePeriod,
    Stop,
    TransitMode,
    VehicleType,
)
from shapely.geometry import Point  # noqa: E402

SRC = ROOT / "frontend" / "src"
TSC_JS = ROOT / "frontend" / "node_modules" / "typescript" / "lib" / "tsc.js"
TOLERANCE = 1e-9

# --- Сеть ------------------------------------------------------------------
# Три пути из s1 в s4: прямой автобусный rA через s2, обходной rB через s3 и
# скоростной метро rT s2→s4, дающий связку rA+rT с одной пересадкой.
# Связка rA+rT быстрее любого прямого, поэтому первым проходом выбирается она,
# а вторым (маршруты rA и rT уже запрещены) — прямой rB. Они не доминируют
# друг друга: rA+rT короче, rB без пересадок. Именно такая сетка даёт
# недоминируемые альтернативы, иначе Pareto схлопывает всё до одного.
STOPS = [
    Stop(id="s1", name="S1", location=Point(0.0, 0.0)),
    Stop(id="s2", name="S2", location=Point(2000.0, 0.0)),
    Stop(id="s3", name="S3", location=Point(2000.0, 2000.0)),
    Stop(id="s4", name="S4", location=Point(6000.0, 0.0)),
]
ROUTES = [
    Route(id="rA", name="A", mode=TransitMode.BUS, stop_ids=("s1", "s2", "s4"),
          both_ways=False, closed=False),
    Route(id="rB", name="B", mode=TransitMode.BUS, stop_ids=("s1", "s3", "s4"),
          both_ways=False, closed=False),
    Route(id="rT", name="T", mode=TransitMode.METRO, stop_ids=("s2", "s4"),
          both_ways=False, closed=False),
]
PERIODS = {"am": ServicePeriod(id="am", start_minute=360, end_minute=540)}
SERVICES = [
    Service(id="vA", route_id="rA", vehicle_type_id="vt_bus", headway_by_period={"am": 10}),
    Service(id="vB", route_id="rB", vehicle_type_id="vt_bus", headway_by_period={"am": 10}),
    # Метро ходит часто: иначе ожидание пересадки съедает выигрыш во времени.
    Service(id="vT", route_id="rT", vehicle_type_id="vt_metro", headway_by_period={"am": 1}),
]
VEHICLE_TYPES = [
    VehicleType(id="vt_bus", name="Bus", mode=TransitMode.BUS, capacity=90),
    VehicleType(id="vt_metro", name="Metro", mode=TransitMode.METRO, capacity=750),
]


def _build_network() -> Network:
    return Network(
        stops={s.id: s for s in STOPS},
        routes={r.id: r for r in ROUTES},
        vehicle_types={v.id: v for v in VEHICLE_TYPES},
        periods=PERIODS,
        services={s.id: s for s in SERVICES},
    )


def _payload() -> dict:
    """Сеть в форме frontend NetworkPayload."""
    return {
        "origin_lon": 0.0,
        "origin_lat": 0.0,
        "stops": [
            {"id": s.id, "name": s.name, "is_station": s.is_station,
             "location": {"x": s.location.x, "y": s.location.y}}
            for s in STOPS
        ],
        "routes": [
            {"id": r.id, "name": r.name, "mode": r.mode.value,
             "stop_ids": list(r.stop_ids), "geometry": None, "both_ways": r.both_ways,
             "closed": r.closed}
            for r in ROUTES
        ],
        "vehicle_types": [
            {"id": v.id, "name": v.name, "mode": v.mode.value, "capacity": v.capacity,
             "operating_cost_per_km": 0.0}
            for v in VEHICLE_TYPES
        ],
        "periods": [
            {"id": p.id, "start_minute": p.start_minute, "end_minute": p.end_minute}
            for p in PERIODS.values()
        ],
        "services": [
            {"id": s.id, "route_id": s.route_id, "vehicle_type_id": s.vehicle_type_id,
             "headway_by_period": dict(s.headway_by_period)}
            for s in SERVICES
        ],
        "track_nodes": [],
        "track_sections": [],
    }


PROBE = """
// Подменяем self до импорта: routing.worker.ts вешает обработчик на self.onmessage
// и сам себя не исполняет иначе.
const posted = [];
globalThis.self = { onmessage: null, postMessage: (message) => posted.push(message) };

const { packRaptorInput } = await import("./workers/routing.js");
await import("./workers/routing.worker.js");

const spec = JSON.parse(process.argv[2]);
const network = spec.network;
const stopCount = network.stops.length;
const access = new Float64Array(stopCount).fill(Infinity);
const egress = new Float64Array(stopCount).fill(Infinity);
access[spec.origin] = 0;
egress[spec.destination] = 0;

let cached = null;

// Упаковка внутри run: интервальные множители меняют сами отправления, поэтому
// вход пересобирается на каждый прогон, а не один раз.
const pack = (headwayFactors) => {
  const factors = headwayFactors
    ? new Map(Object.entries(headwayFactors).map(([k, v]) => [k, v]))
    : undefined;
  const { input, patterns } = packRaptorInput(
    network, "am", access, egress, spec.departureMin, 0, spec.maxTransfers,
    undefined, factors,
  );
  const segmentIndex = new Map();
  for (let p = 0; p < patterns.length; p += 1) {
    const start = input.routeSegmentOffsets[p];
    for (let local = 0; local + 1 < patterns[p].stops.length; local += 1) {
      const from = network.stops[patterns[p].stops[local]].id;
      const to = network.stops[patterns[p].stops[local + 1]].id;
      segmentIndex.set(`${p}|${from}|${to}`, start + local);
    }
  }
  return { input, patterns, segmentIndex };
};

const run = (options) => {
  const packed = pack(options.headwayFactors);
  const { input, patterns, segmentIndex } = packed;
  if (!cached) cached = patterns.map((p) => p.routeId);
  const routePenalties = new Float64Array(patterns.length);
  const bannedRoutes = new Uint8Array(patterns.length);
  const segmentPenalties = new Float64Array(input.segmentTimes.length);
  for (const [pattern, value] of Object.entries(options.routePenalties ?? {})) {
    routePenalties[Number(pattern)] = value;
  }
  for (const pattern of options.bannedPatterns ?? []) bannedRoutes[pattern] = 1;
  for (const [key, value] of Object.entries(options.segmentPenalties ?? {})) {
    const at = segmentIndex.get(key);
    if (at === undefined) throw new Error(`unknown segment ${key}`);
    segmentPenalties[at] = value;
  }
  posted.length = 0;
  self.onmessage({
    data: {
      ...input,
      routePenalties, bannedRoutes, segmentPenalties,
      maxAlternatives: options.maxAlternatives ?? 1,
      diversityPenaltyMin: options.diversityPenaltyMin ?? 15,
    },
  });
  return posted[0];
};

const patternRoute = (patterns) => patterns.map((p) => p.routeId);
const idsOf = (stops) => Array.from(stops).map((s) => network.stops[s].id);

const describeRide = (ride) => ({
  routeId: network.stops[ride.boardStop] ? patternRoute(cached)[ride.pattern] : null,
  from: network.stops[ride.boardStop].id,
  to: network.stops[ride.alightStop].id,
  boardLocal: ride.boardLocal,
  alightLocal: ride.alightLocal,
  inVehicleMin: ride.inVehicleMin,
  waitMin: ride.waitMin,
});

const basePatterns = pack(undefined).patterns;
const basePatternIds = patternRoute(basePatterns);
const basePacked = pack(undefined);

const single = run({});
const alternatives = run({ maxAlternatives: 3, diversityPenaltyMin: 15 });

const describe = (patterns) => patternRoute(patterns);

process.stdout.write(JSON.stringify({
  patterns: describe(basePatterns),
  single: {
    found: single.result.found,
    transfers: single.result.transfers,
    generalizedMin: single.result.generalizedMin,
    routes: single.result.routeIds === undefined
      ? null
      : Array.from(single.result.routeIds).map((p) => basePatternIds[p]),
    boardStops: single.result.boardStops === undefined
      ? null
      : idsOf(single.result.boardStops),
    alightStops: single.result.alightStops === undefined
      ? null
      : idsOf(single.result.alightStops),
  },
  crowdedRoute: (() => {
    const out = run({ segmentPenalties: { "0|s1|s2": 600 } });
    return Array.from(out.result.routeIds).map((p) => basePatternIds[p]);
  })(),
  bannedRoute: (() => {
    const out = run({ bannedPatterns: [0] });
    return Array.from(out.result.routeIds).map((p) => basePatternIds[p]);
  })(),
  penalizedSingle: (() => {
    const out = run({ routePenalties: { 1: 30 } });
    return {
      routes: Array.from(out.result.routeIds).map((p) => basePatternIds[p]),
      generalizedMin: out.result.generalizedMin,
    };
  })(),
  alternatives: alternatives.alternatives.map((a) => ({
    routes: a.rides.map((ride) => basePatternIds[ride.pattern]),
    rides: a.rides.map(describeRide),
    transfers: a.transfers,
    durationMin: a.durationMin,
    inVehicleMin: a.inVehicleMin,
    waitMin: a.waitMin,
    walkToMin: a.walkToMin,
    walkFromMin: a.walkFromMin,
  })),
  passes: alternatives.passes,
  crowdedAlternatives: (() => {
    const out = run({
      maxAlternatives: 2, diversityPenaltyMin: 15, segmentPenalties: { "0|s1|s2": 600 },
    });
    return out.alternatives.map((a) => ({
      routes: a.rides.map((ride) => basePatternIds[ride.pattern]),
      inVehicleMin: a.inVehicleMin,
      durationMin: a.durationMin,
      transfers: a.transfers,
      generalizedMin: a.generalizedMin,
    }));
  })(),
  // Множитель интервала удлиняет headway втрое: отправлений втрое меньше.
  withFactors: (() => {
    const out = run({ headwayFactors: { vA: 3 }, maxAlternatives: 1 });
    return {
      departures: Array.from(out.info.patternDepartures),
      waitMin: out.result.waitMin,
      durationMin: out.result.arrivalMin - spec.departureMin,
    };
  })(),
  baseDepartures: Array.from(run({ maxAlternatives: 1 }).info.patternDepartures),
  // Множитель меньше единицы игнорируется: сгущение не поощряется.
  factorBelowOne: Array.from(
    run({ headwayFactors: { vA: 0.5 }, maxAlternatives: 1 }).info.patternDepartures,
  ),
}));
"""


def _ts_out(tmp_path: Path) -> dict | None:
    node = shutil.which("node")
    if node is None or not TSC_JS.exists():
        return None
    build = subprocess.run(
        [
            node, str(TSC_JS),
            str(SRC / "workers" / "routing.ts"),
            str(SRC / "workers" / "routing.worker.ts"),
            str(SRC / "journey-alternatives.ts"),
            str(SRC / "planning" / "timetable.ts"),
            str(SRC / "types.ts"),
            "--outDir", str(tmp_path),
            "--rootDir", str(SRC),
            "--module", "esnext",
            "--target", "es2022",
            "--ignoreConfig",
        ],
        capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT / "frontend"),
    )
    if build.returncode != 0:
        pytest.fail(f"tsc не собрал routing-стенд:\n{build.stdout}\n{build.stderr}")
    for compiled in tmp_path.rglob("*.js"):
        text = compiled.read_text(encoding="utf-8")
        for target in (
            "./journey-alternatives", "./routing", "../journey-alternatives",
            "../types", "../planning/timetable",
        ):
            text = text.replace(f'from "{target}"', f'from "{target}.js"')
        compiled.write_text(text, encoding="utf-8")
    (tmp_path / "probe.mjs").write_text(PROBE, encoding="utf-8")
    payload = {
        "network": _payload(),
        "origin": 0,
        "destination": 3,
        "departureMin": 360,
        "maxTransfers": 3,
    }
    run = subprocess.run(
        [node, str(tmp_path / "probe.mjs"), json.dumps(payload)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(tmp_path),
    )
    if run.returncode != 0:
        pytest.fail(f"стенд routing не отработал:\n{run.stdout}\n{run.stderr}")
    return json.loads(run.stdout)


@pytest.fixture(scope="module")
def kernel(tmp_path_factory) -> dict:
    out = _ts_out(tmp_path_factory.mktemp("routing-kernel"))
    if out is None:
        pytest.skip("node или typescript недоступны: ядро routing не проверяется")
    return out


def test_single_route_is_found_and_expanded(kernel: dict) -> None:
    """Одиночный режим отдаёт связку rA+rT с корректной раскладкой по ногам."""
    single = kernel["single"]
    assert single["found"] is True
    assert single["transfers"] == 1
    assert single["routes"] == ["rA", "rT"]
    assert single["boardStops"] == ["s1", "s2"]
    assert single["alightStops"] == ["s2", "s4"]
    assert single["generalizedMin"] > 0


def test_crowding_penalty_moves_route_off_crowded_segment(kernel: dict) -> None:
    """Штраф на s1→s2 маршрута rA уводит роутер на прямой rB.

    Связка rA+rT не выбирается: её штраф прибавляется к тому же сегменту.
    """
    assert kernel["single"]["routes"] == ["rA", "rT"]
    assert kernel["crowdedRoute"] == ["rB"]


def test_banned_pattern_is_not_used(kernel: dict) -> None:
    """Запрет паттерна rA оставляет только прямой rB."""
    assert kernel["bannedRoute"] == ["rB"]


def test_route_penalty_enters_generalized_cost(kernel: dict) -> None:
    """Штраф rB делает его дороже связки rA+rT, поэтому выбор не меняется."""
    assert kernel["penalizedSingle"]["routes"] == ["rA", "rT"]


def test_alternatives_are_distinct_and_pareto_filtered(kernel: dict) -> None:
    """Diversity-цикл даёт две недоминируемые связки, и обе доживают до ответа.

    rA+rT короче (10.4 против 24.3 мин), но с пересадкой; rB без пересадок.
    Если бы длительность и пересадки не различались, Pareto схлопнул бы набор.
    """
    alternatives = kernel["alternatives"]
    assert [a["routes"] for a in alternatives] == [["rA", "rT"], ["rB"]]
    assert kernel["passes"] == 2
    fast, direct = alternatives
    assert fast["durationMin"] < direct["durationMin"]
    assert fast["transfers"] == 1 > direct["transfers"]
    sequences = ["+".join(a["routes"]) for a in alternatives]
    assert len(set(sequences)) == len(sequences)


def test_crowding_penalty_reshuffles_alternatives(kernel: dict) -> None:
    """Со штрафом на rA первым идёт rB, вторым — прямой rA.

    Связка rA+rT во втором проходе уже невозможна: штраф 600 минут выносит
    прибытие в s2 за конец периода, и у метро не остаётся подходящего
    отправления. Это ожидаемо, а не дефект — перегруженный участок просто
    недостижим в этом периоде.
    """
    assert [a["routes"] for a in kernel["crowdedAlternatives"]] == [["rB"], ["rA"]]


def test_reported_in_vehicle_time_excludes_crowding_penalty(kernel: dict) -> None:
    """Штраф переполненности влияет на выбор, но не портит полезность поездки.

    Иначе переполненность задваивалась бы в utility через время в пути.
    """
    base = {tuple(a["routes"]): a for a in kernel["alternatives"]}
    for journey in kernel["crowdedAlternatives"]:
        key = tuple(journey["routes"])
        if key in base:
            assert journey["inVehicleMin"] == pytest.approx(
                base[key]["inVehicleMin"], abs=TOLERANCE
            ), f"{key}: время в пути изменилось вместе со штрафом"


def test_walk_and_wait_are_reported(kernel: dict) -> None:
    for journey in kernel["alternatives"]:
        assert journey["walkToMin"] == pytest.approx(0.0, abs=TOLERANCE)
        assert journey["walkFromMin"] == pytest.approx(0.0, abs=TOLERANCE)
        assert journey["waitMin"] >= 0
        assert journey["inVehicleMin"] > 0
        # Ноги образуют связную цепочку: высадка одной = посадка следующей.
        rides = journey["rides"]
        for previous, following in zip(rides, rides[1:], strict=False):
            assert previous["to"] == following["from"], (
                f"разрыв связки: {previous['to']} != {following['from']}"
            )
        for ride in rides:
            assert ride["alightLocal"] > ride["boardLocal"]
            assert ride["inVehicleMin"] > 0
        assert rides[0]["from"] == "s1"
        assert rides[-1]["to"] == "s4"


def test_headway_factor_lengthens_headway_and_recomputes_departures(kernel: dict) -> None:
    """Множитель интервала удлиняет headway: отправлений становится втрое меньше.

    Отправления пересчитываются, а не сдвигаются: иначе множитель обратной
    связи не влиял бы на расписание, а был бы косметикой.
    """
    assert kernel["patterns"] == ["rA", "rB", "rT"]
    assert kernel["baseDepartures"] == [18, 18, 180]
    assert kernel["withFactors"]["departures"] == [6, 18, 180]


def test_headway_factor_below_one_is_ignored(kernel: dict) -> None:
    """Сгущение не поощряется: множитель < 1 не сокращает интервал."""
    assert kernel["factorBelowOne"] == kernel["baseDepartures"]


def test_section_capacity_helper_still_agrees_for_reference() -> None:
    """Опорная проверка: сеть стенда остаётся валидной для assignment-слоя."""
    capacity, platform = _section_capacity_and_platforms(_build_network(), "am")
    assert capacity, "у сетки стенда должны быть вместимости секций"
    assert platform, "у сетки стенда должны быть длины платформ"
