"""Тесты цикла итераций assignment.worker в Node.

Цикл переносит `assign.py::assign_demand`: повторяет `assignOnce`, смешивает
штрафы переполненности и интервальные множители с `damping` и выходит по
`convergence_tolerance`. Роутеры TS и Python различаются намеренно, поэтому
численные потоки здесь не сверяются с эталоном — это уже сделано в
`test_assignment_core_parity.py` на подставленных journey. Здесь проверяется
поведение цикла: сходимость, влияние обратной связи, корректность учёта
нагрузки по секциям и явные ошибки вместо молчаливых нулей.

Стенд собирает настоящие модули локальным tsc. Без node/typescript тест
пропускается.
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

# Сеть: линейная ось s1…s5, два параллельных автобусных маршрута и метро,
# соединяющее середину с концом. Зоны лежат вдоль оси, поэтому у каждой есть
# ближайшая остановка в пределах access_m.
STOPS = [
    Stop(id=f"s{i}", name=f"S{i}", location=Point(float(i - 1) * 1000.0, 0.0))
    for i in range(1, 6)
]
ROUTES = [
    Route(id="rA", name="A", mode=TransitMode.BUS,
          stop_ids=("s1", "s2", "s3", "s4", "s5"), both_ways=False, closed=False),
    Route(id="rB", name="B", mode=TransitMode.BUS,
          stop_ids=("s1", "s3", "s5"), both_ways=False, closed=False),
    Route(id="rT", name="T", mode=TransitMode.METRO,
          stop_ids=("s3", "s5"), both_ways=False, closed=False),
]
PERIODS = {"am": ServicePeriod(id="am", start_minute=360, end_minute=540)}
SERVICES = [
    Service(id="vA", route_id="rA", vehicle_type_id="vt_bus", headway_by_period={"am": 10}),
    Service(id="vB", route_id="rB", vehicle_type_id="vt_bus", headway_by_period={"am": 10}),
    Service(id="vT", route_id="rT", vehicle_type_id="vt_metro", headway_by_period={"am": 1}),
]
VEHICLE_TYPES = [
    VehicleType(id="vt_bus", name="Bus", mode=TransitMode.BUS, capacity=90),
    VehicleType(id="vt_metro", name="Metro", mode=TransitMode.METRO, capacity=750),
]
# Зоны: по одной у каждой остановки, кроме первой, у которой две зоны, чтобы
# появилась конкуренция за один маршрут и переполненность.
ZONES = [
    {"id": "z1", "centroidX": 0.0, "centroidY": 0.0, "population": 6000.0, "jobs": 500.0, "noCarShare": 0.7},
    {"id": "z1b", "centroidX": 100.0, "centroidY": 0.0, "population": 4000.0, "jobs": 200.0, "noCarShare": 0.5},
    {"id": "z2", "centroidX": 1000.0, "centroidY": 0.0, "population": 3000.0, "jobs": 300.0, "noCarShare": 0.4},
    {"id": "z3", "centroidX": 2000.0, "centroidY": 0.0, "population": 2500.0, "jobs": 800.0, "noCarShare": 0.6},
    {"id": "z5", "centroidX": 4000.0, "centroidY": 0.0, "population": 4000.0, "jobs": 2500.0, "noCarShare": 0.3},
]
PAIRS = [
    {"originZoneId": "z1", "destinationZoneId": "z5", "tripsPerDay": 2500.0, "baseTimeMin": 22.0},
    {"originZoneId": "z1b", "destinationZoneId": "z5", "tripsPerDay": 1800.0, "baseTimeMin": 20.0},
    {"originZoneId": "z2", "destinationZoneId": "z5", "tripsPerDay": 900.0, "baseTimeMin": 15.0},
    {"originZoneId": "z3", "destinationZoneId": "z5", "tripsPerDay": 600.0, "baseTimeMin": 10.0},
]


def _payload() -> dict:
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
             "stop_ids": list(r.stop_ids), "geometry": None,
             "both_ways": r.both_ways, "closed": r.closed}
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
import { assignDemand } from "./workers/assignment-runtime.js";

const spec = JSON.parse(process.argv[2]);
const base = {
  job: 1,
  network: spec.network,
  periodId: "am",
  pairs: spec.pairs,
  zones: spec.zones,
  // Время отправления намеренно не совпадает с началом интервала: при
  // departureMin = 360 ожидание всегда нулевое, и его нельзя проверить.
  departureMin: 363,
  rangeWindowMin: 0,
  maxTransfers: 3,
};

const run = (overrides) => assignDemand({ ...base, ...overrides });

const six = run({ config: { iterations: 6 } });
const one = run({ config: { iterations: 1 } });
const loose = run({ config: { iterations: 6, convergenceTolerance: 1e-9 } });
const withFare = run({ config: { iterations: 3, transitFare: 2.5 }, choice: { transitFareWeight: 1.0 } });
const fareOnly = run({ config: { iterations: 3, transitFare: 2.5 } });
// База для сравнения тарифа: те же три итерации, но без тарифа. Иначе разницу
// дали бы итерации, а не цена.
const baseThree = run({ config: { iterations: 3 } });
// damping = 1 — это прямая подстановка целевых штрафов без сглаживания.
// Сравнение с damping = 0.5 показывает, что сглаживание действительно работает.
const fullDamping = run({ config: { iterations: 3, damping: 1 } });
// Одна пара, чтобы ожидание можно было проверить точно: связка rA+rT состоит
// из трёх сегментов, и ожидание обязано попасть в счёт ровно один раз.
const singlePair = run({
  pairs: [spec.pairs[0]],
  config: { iterations: 2 },
});

// Малый спрос не создаёт переполненности: штрафы остаются нулевыми, обратная
// связь сходится, и цикл обязан выйти раньше лимита итераций.
const converged = run({
  config: { iterations: 12 },
  pairs: spec.pairs.map((p) => ({ ...p, tripsPerDay: p.tripsPerDay / 200.0 })),
});

// Пустой спрос обязан дать нули, а не исключение.
const empty = run({ pairs: [], config: { iterations: 2 } });

// Зона вне reach ни одной остановки: маршрута нет, спрос не назначен.
const unreachable = run({
  pairs: [{ originZoneId: "zfar", destinationZoneId: "z5", tripsPerDay: 500.0, baseTimeMin: 30.0 }],
  zones: [...spec.zones, { id: "zfar", centroidX: 9000.0, centroidY: 9000.0, population: 500.0, jobs: 10.0, noCarShare: 0.5 }],
  config: { iterations: 2 },
});

const slim = (r) => ({
  metrics: r.metrics,
  iterations: r.iterations,
  maxLoadRatio: r.maxLoadRatio,
  unserved: r.unserved,
  unroutedPairs: r.unroutedPairs,
  lossReasons: r.lossReasons,
  sectionLoads: r.sectionLoads,
  routeFlows: r.routeFlows,
  stopFlows: r.stopFlows.map((s) => [s.stopId, s.boardings, s.alightings, s.transfers]),
  headwayFactors: r.serviceHeadwayFactors,
});

process.stdout.write(JSON.stringify({
  six: slim(six),
  one: slim(one),
  loose: slim(loose),
  withFare: slim(withFare),
  fareOnly: slim(fareOnly),
  baseThree: slim(baseThree),
  fullDamping: slim(fullDamping),
  singlePair: slim(singlePair),
  converged: slim(converged),
  empty: slim(empty),
  unreachable: slim(unreachable),
  errors: (() => {
    const cases = {};
    try { run({ periodId: "nope" }); cases.unknownPeriod = null; }
    catch (e) { cases.unknownPeriod = e.message; }
    try { run({ config: { iterations: 0 } }); cases.zeroIterations = null; }
    catch (e) { cases.zeroIterations = e.message; }
    try { run({ config: { damping: 0 } }); cases.zeroDamping = null; }
    catch (e) { cases.zeroDamping = e.message; }
    return cases;
  })(),
}));
"""


def _ts_out(tmp_path: Path) -> dict | None:
    node = shutil.which("node")
    if node is None or not TSC_JS.exists():
        return None
    sources = [
        "workers/assignment-runtime.ts",
        "workers/routing-kernel.ts",
        "workers/routing.ts",
        "journey-alternatives.ts",
        "assignment-model.ts",
        "assignment-network.ts",
        "choice.ts",
        "planning/timetable.ts",
        "types.ts",
    ]
    build = subprocess.run(
        [
            node, str(TSC_JS), *[str(SRC / s) for s in sources],
            "--outDir", str(tmp_path),
            "--rootDir", str(SRC),
            "--module", "esnext",
            "--target", "es2022",
            "--ignoreConfig",
        ],
        capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT / "frontend"),
    )
    if build.returncode != 0:
        pytest.fail(f"tsc не собрал стенд assignment:\n{build.stdout}\n{build.stderr}")
    for compiled in tmp_path.rglob("*.js"):
        text = compiled.read_text(encoding="utf-8")
        for target in (
            "./journey-alternatives", "./routing", "./routing-kernel", "./choice",
            "./assignment-model", "./assignment-network",
            "../journey-alternatives", "../assignment-model", "../assignment-network",
            "../choice", "../types", "../planning/timetable",
        ):
            text = text.replace(f'from "{target}"', f'from "{target}.js"')
        compiled.write_text(text, encoding="utf-8")
    (tmp_path / "probe.mjs").write_text(PROBE, encoding="utf-8")
    payload = {"network": _payload(), "zones": ZONES, "pairs": PAIRS}
    run = subprocess.run(
        [node, str(tmp_path / "probe.mjs"), json.dumps(payload)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(tmp_path),
    )
    if run.returncode != 0:
        pytest.fail(f"стенд assignment не отработал:\n{run.stdout}\n{run.stderr}")
    return json.loads(run.stdout)


@pytest.fixture(scope="module")
def assignment(tmp_path_factory) -> dict:
    out = _ts_out(tmp_path_factory.mktemp("assignment-loop"))
    if out is None:
        pytest.skip("node или typescript недоступны: цикл assignment не проверяется")
    return out


def test_metrics_add_up_to_total_demand(assignment: dict) -> None:
    metrics = assignment["six"]["metrics"]
    total = sum(pair["tripsPerDay"] for pair in PAIRS)
    assert metrics["totalTrips"] == pytest.approx(total, abs=TOLERANCE)
    assert metrics["transitShare"] == pytest.approx(
        metrics["transitTrips"] / metrics["totalTrips"], abs=TOLERANCE
    )
    modes = sum(
        metrics[key] for key in ("transitTrips", "carTrips", "walkTrips", "bikeTrips", "restTrips")
    )
    assert modes == pytest.approx(total, abs=1e-6), "доли режимов не дают суммарный спрос"


def test_every_demand_pair_is_routed(assignment: dict) -> None:
    assert assignment["six"]["unroutedPairs"] == 0
    assert assignment["six"]["unserved"] == 0.0


def test_section_loads_are_consistent_with_demand(assignment: dict) -> None:
    """Сумма пассажиропотоков по секциям согласуется с транзитным спросом.

    Одна поездка даёт столько проходов, сколько сегментов она проходит, поэтому
    сумма потоков не равна спросу, а лежит между ним и спросом, умноженным на
    максимальную длину связки в сегментах.
    """
    sections = assignment["six"]["sectionLoads"]
    assert sections, "секции должны быть посчитаны"
    metrics = assignment["six"]["metrics"]
    traversals = sum(section["passengers"] for section in sections)
    transit = metrics["transitTrips"]
    assert traversals >= transit - TOLERANCE, (
        f"нагрузка {traversals} меньше транзитного спроса {transit}"
    )
    # Самая длинная связка в этой сетке: rA из пяти остановок — четыре сегмента.
    assert traversals <= transit * 4 + 1e-6, (
        f"нагрузка {traversals} неправдоподобно велика для спроса {transit}"
    )
    for section in sections:
        expected = 0.0 if section["capacity"] <= 0 else section["passengers"] / section["capacity"]
        assert section["loadRatio"] == pytest.approx(expected, abs=TOLERANCE)
        assert section["deniedBoardings"] >= 0.0
        if section["loadRatio"] >= 4.0:
            assert section["crowdingLevel"] == "extreme"
        elif section["loadRatio"] >= 2.0:
            assert section["crowdingLevel"] == "severe"
        elif section["loadRatio"] >= 1.0:
            assert section["crowdingLevel"] == "crowded"
        else:
            assert section["crowdingLevel"] == "normal"


def test_boardings_equal_alightings_over_the_network(assignment: dict) -> None:
    """Совокупные посадки и высадки по сети обязаны совпадать."""
    flows = assignment["six"]["stopFlows"]
    boardings = sum(row[1] for row in flows)
    alightings = sum(row[2] for row in flows)
    assert boardings == pytest.approx(alightings, abs=1e-6), (
        f"посадки {boardings} != высадки {alightings}"
    )


def test_route_flows_cover_every_route(assignment: dict) -> None:
    assert [row["routeId"] for row in assignment["six"]["routeFlows"]] == [
        route.id for route in ROUTES
    ]


def test_iterations_stay_within_the_configured_limit(assignment: dict) -> None:
    assert assignment["one"]["iterations"] == 1
    assert 1 <= assignment["six"]["iterations"] <= 6
    assert 1 <= assignment["loose"]["iterations"] <= 6


def test_loop_converges_before_the_iteration_limit(assignment: dict) -> None:
    """Сходимость обязана выключать цикл раньше лимита.

    Малый спрос не даёт переполненности, штрафы остаются нулевыми, поэтому
    изменение обратной связи падает ниже порога. Если условие сходимости
    отключить, цикл отработает все 12 итераций.
    """
    assert assignment["converged"]["iterations"] < 12, (
        f"цикл не сошёлся за 12 итераций: {assignment['converged']['iterations']}"
    )
    assert assignment["converged"]["maxLoadRatio"] < 1.0, "сходимость проверяется без перегрузки"


def test_wait_time_is_reported(assignment: dict) -> None:
    """Ожидание попадает в метрики ровно один раз на рейс.

    Отправление сдвинуто на 363-ю минуту, первый автобус уходит в 370-ю, и
    метро ходит раз в минуту. Среднее ожидание — взвешенная смесь этих двух
    случаев.

    Значение ниже - golden, выведенный из расписания, а не произвольное число:
    оно ловит задваивание ожидания по сегментам рейса. Оно изменилось с 5.195
    на 0.947 вместе со сменой модели выбора: по правилу игры доля поездок,
    поехавших на медленный автобус с семиминутным ожиданием, упала, и
    взвешенное среднее сместилось к поездам метро с ожиданием около нуля.
    """
    wait = assignment["six"]["metrics"]["averageWaitTimeMin"]
    assert wait == pytest.approx(0.947, abs=0.05), (
        f"среднее ожидание {wait} изменилось после смены модели выбора"
    )
    # Ожидание не может превышать интервал: пассажир садится в первый же рейс.
    assert 0.0 < wait <= 10.0, f"ожидание {wait} вне диапазона (0, headway]"
    assert assignment["empty"]["metrics"]["averageWaitTimeMin"] == pytest.approx(0.0, abs=TOLERANCE)


def test_single_iteration_park_short_demand_in_rest(assignment: dict) -> None:
    """Сходимость assignment зависит от числа итераций сильнее, чем раньше.

    Цикл пересчитывает headway-факторы по накопленной нагрузке, а новое
    правило выбора решает пару целиком, без размытия логитом. Поэтому
    на первой итерации, когда оценка ожидания ещё не «научена» загрузкой,
    5200 из 5800 поездок оказываются дешевле отказа от поездки, чем
    поездка на метро, и уходят в rest. К шестой итерации факторы
    устаканиваются, и транзит забирает их все.

    Раньше логит давал транзиту долю при любых стоимостях, поэтому такой
    зависимости от итераций не было видно. Это наблюдение, а не норма:
    при недоработанной сходимости распределение поездок закладывается
    неверно.
    """
    one = assignment["one"]["metrics"]
    six = assignment["six"]["metrics"]
    assert one["transitTrips"] < six["transitTrips"]
    assert one["restTrips"] > 0.0
    assert six["restTrips"] == pytest.approx(0.0, abs=TOLERANCE)


def test_damping_smooths_the_feedback(assignment: dict) -> None:
    """damping обязан влиять на результат: без сглаживания штрафы прыгают.

    damping = 1 — это прямая подстановка целевых штрафов, damping = 0.5
    подводит их постепенно. Если сглаживание не работает, прогоны совпадут.
    """
    smoothed = assignment["baseThree"]
    direct = assignment["fullDamping"]
    assert smoothed["metrics"]["transitShare"] != pytest.approx(
        direct["metrics"]["transitShare"], abs=1e-3
    ), "damping не влияет на распределение — обратная связь применяется напрямую"
    assert smoothed["maxLoadRatio"] != pytest.approx(direct["maxLoadRatio"], abs=1e-3)
    # Прямая подстановка загоняет штрафы в максимум сразу и оставляет секции
    # недогруженными; сглаживание даёт промежуточную картину.
    assert direct["maxLoadRatio"] < smoothed["maxLoadRatio"]


def test_crowding_feedback_changes_the_result(assignment: dict) -> None:
    """Обратная связь обязана менять назначение, а не быть декоративной.

    Первая итерация не учитывает переполненность: роутер выбирает кратчайший
    путь и набивает его. Последующие итерации поднимают штрафы и уводят часть
    спроса, поэтому результат обязан отличаться.
    """
    six = assignment["six"]["metrics"]
    one = assignment["one"]["metrics"]
    assert assignment["one"]["maxLoadRatio"] < assignment["six"]["maxLoadRatio"], (
        "шесть итераций обязаны признать переполненность сильнее одной"
    )
    assert one["transitTrips"] != pytest.approx(six["transitTrips"], abs=1.0), (
        "перераспределение спроса не изменило назначение — обратная связь не работает"
    )


def test_headway_factors_are_at_least_one(assignment: dict) -> None:
    """Обратная связь по интервалам может только ухудшать интервал."""
    factors = assignment["six"]["headwayFactors"]
    assert factors, "множители интервалов должны быть посчитаны"
    for service_id, factor in factors:
        assert factor >= 1.0 - TOLERANCE, f"{service_id}: множитель {factor} < 1"


def test_fare_reduces_transit_share(assignment: dict) -> None:
    """Тариф снижает долю транзита напрямую.

    Раньше это было не так: при `transit_fare_weight = 0` логит полностью
    игнорировал тариф, и первая проверка закрепляла именно это допущение.
    По правилу игры тариф входит в обобщённую стоимость транзита напрямую
    (`транзит = t_raptor * VOT + fare`), поэтому нулевая транзитная доля
    больше не означает, что тариф на неё не влияет.
    """
    base = assignment["baseThree"]["metrics"]["transitShare"]
    fare_only = assignment["fareOnly"]["metrics"]["transitShare"]
    with_fare = assignment["withFare"]["metrics"]["transitShare"]
    assert base > 0.0
    assert fare_only < base, "тариф обязан снижать долю транзита"
    assert with_fare < base


def test_empty_demand_is_all_zeros(assignment: dict) -> None:
    metrics = assignment["empty"]["metrics"]
    assert metrics["totalTrips"] == 0.0
    assert metrics["transitTrips"] == 0.0
    assert metrics["transitShare"] == 0.0
    assert assignment["empty"]["unroutedPairs"] == 0


def test_unreachable_zone_leaves_demand_unassigned(assignment: dict) -> None:
    """Зона вне reach не получает транзита.

    Транзит недоступен, поэтому он исключается из сравнения, а не получает
    нулевое время: раньше, при передаче None как нуля, он выигрывал бы у всех
    и получал 100% даже в городе без единой доступной линии.

    Дальше выигрывает не авто, а отказ от поездки. Зона zfar в 10.3 км от
    z5: авто стоит 23.5 (12.4 мин езды + постоянные 576 с парковки в
    воспринимаемых минутах + 11.7 евро на километры и парковку), а
    `baseTimeMin = 30` обходится в 16.1 - дешевле. Раньше логит размазывал
    спрос по режимам, и доставало авто.
    """
    result = assignment["unreachable"]
    assert result["unroutedPairs"] == 1
    assert result["metrics"]["transitTrips"] == pytest.approx(0.0, abs=TOLERANCE)
    assert result["metrics"]["carTrips"] == pytest.approx(0.0, abs=TOLERANCE)
    assert result["metrics"]["restTrips"] > 0.0
    assert result["unserved"] == pytest.approx(0.0, abs=TOLERANCE)


def test_invalid_configuration_raises(assignment: dict) -> None:
    errors = assignment["errors"]
    assert errors["unknownPeriod"], "неизвестный период обязан дать ошибку"
    assert errors["zeroIterations"], "iterations <= 0 обязан дать ошибку"
    assert errors["zeroDamping"], "damping вне (0, 1] обязан дать ошибку"
