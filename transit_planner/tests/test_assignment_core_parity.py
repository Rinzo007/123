"""Паритет ядра assignment: TS assignment-model.ts ↔ Python _assign_once.

Python — эталон. Роутеры намеренно различаются (TS RAPTOR — независимое
приближение), поэтому сравнивается не поиск пути, а сам расчёт потоков:
на обе стороны подаются ОДИНАКОВЫЕ journey одной и той же конфигурации
транзита. Проверяются mode split, метрики, потоки по маршрутам, остановкам и
секциям, strict-fit boarding (denied_boardings), классификация потерь и
обратная связь по переполненности/интервалам.

Модуль assignment-model.ts тянет только ./choice, поэтому стенд собирает оба
файла и правит extensionless-импорты. Без node/typescript тест пропускается.
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

from transit_planner.assignment import (  # noqa: E402
    AssignmentConfig,
    SectionLoad,
    _assign_once,
    _segment_crowding_penalties,
    _max_penalty_delta,
)
from transit_planner.choice import ChoiceConfig  # noqa: E402
from transit_planner.city import DemandZone  # noqa: E402
from transit_planner.demand import DemandMatrix, ODPairDemand  # noqa: E402
from transit_planner.routing import Journey, JourneyLeg, RouterConfig, TransitRouter  # noqa: E402
from transit_planner.network import (  # noqa: E402
    Network,
    TransitMode,
    Route,
    Service,
    ServicePeriod,
    Stop,
    VehicleType,
)
from shapely.geometry import Point  # noqa: E402

SRC = ROOT / "frontend" / "src"
TSC_JS = ROOT / "frontend" / "node_modules" / "typescript" / "lib" / "tsc.js"
TOLERANCE = 1e-9

# --- Сеть эталона -------------------------------------------------------
# Два маршрута: r1 прямой, r2 с пересадкой, плюс неиспользуемый r3, чтобы
# route_flows/stop_flows обязаны были отчитаться и по пустым объектам.
STOPS = [
    Stop(id="s1", name="S1", location=Point(0.0, 0.0), is_station=False),
    Stop(id="s2", name="S2", location=Point(1000.0, 0.0), is_station=False),
    Stop(id="s3", name="S3", location=Point(2000.0, 0.0), is_station=False),
    Stop(id="s4", name="S4", location=Point(3000.0, 0.0), is_station=False),
    Stop(id="s5", name="S5", location=Point(4000.0, 0.0), is_station=False),
]
ROUTES = [
    Route(id="r1", name="R1", mode=TransitMode.BUS, stop_ids=("s1", "s2", "s3"), both_ways=False, closed=False),
    Route(id="r2", name="R2", mode=TransitMode.TRAM, stop_ids=("s3", "s4"), both_ways=False, closed=False),
    Route(id="r3", name="R3", mode=TransitMode.BUS, stop_ids=("s1", "s4"), both_ways=False, closed=False),
    Route(id="r4", name="R4", mode=TransitMode.BUS, stop_ids=("s4", "s1"), both_ways=False, closed=False),
    Route(id="r5", name="R5", mode=TransitMode.BUS, stop_ids=("s1", "s5"), both_ways=False, closed=False),
]
PERIODS = {"am": ServicePeriod(id="am", start_minute=360, end_minute=540)}
# Интервалы и тиражи подобраны так, чтобы вместимость была заведомо меньше
# спроса: период 180 мин, headway 180 → 1 рейс, capacity 10 → 10 мест на
# секцию. Strict-fit boarding срабатывает без ручной подмены вместимости,
# которую эталонный _assign_once всё равно считал бы сам.
SERVICES = [
    Service(id="v1", route_id="r1", vehicle_type_id="vt_tiny", headway_by_period={"am": 180}),
    Service(id="v2", route_id="r2", vehicle_type_id="vt_tram", headway_by_period={"am": 90}),
    Service(id="v3", route_id="r3", vehicle_type_id="vt_bus", headway_by_period={"am": 30}),
    Service(id="v4", route_id="r4", vehicle_type_id="vt_bus", headway_by_period={"am": 30}),
    Service(id="v5", route_id="r5", vehicle_type_id="vt_bus", headway_by_period={"am": 30}),
]
VEHICLE_TYPES = [
    VehicleType(id="vt_tiny", name="Minibus", mode=TransitMode.BUS, capacity=10),
    VehicleType(id="vt_tram", name="Tram", mode=TransitMode.TRAM, capacity=250),
    VehicleType(id="vt_bus", name="Bus", mode=TransitMode.BUS, capacity=5),
]

ZONES = {
    "z1": DemandZone("z1", 0.0, 0.0, population=5000.0, jobs=200.0),
    "z2": DemandZone("z2", 1000.0, 0.0, population=3000.0, jobs=100.0),
    "z3": DemandZone("z3", 3000.0, 0.0, population=2000.0, jobs=900.0),
    "z4": DemandZone("z4", 4000.0, 0.0, population=1500.0, jobs=300.0),
}

# --- Пары спроса --------------------------------------------------------
# z1→z3 через пересадку, z1→z1 без маршрута, z2→z3 с низкой долей транзита,
# z1→z2 с нулевым спросом (должен быть пропущен целиком), и — ради покрытия
# strict-fit — z3→z1, садящаяся в s4, где другие пары высаживают пассажиров.
# Без этой пары слагаемое «выгрузка на посадке» всегда равно нулю.
PAIRS = [
    ODPairDemand("z1", "z3", 1200.0, base_time_min=32.0),
    ODPairDemand("z1", "z1", 400.0, base_time_min=5.0),
    ODPairDemand("z2", "z3", 300.0, base_time_min=26.0),
    ODPairDemand("z1", "z2", 0.0, base_time_min=12.0),
    ODPairDemand("z1", "z4", 500.0, base_time_min=34.0),
    ODPairDemand("z3", "z1", 700.0, base_time_min=33.0),
]

# --- Journey, одинаковые на обеих сторонах -------------------------------
# Первая пара получает две альтернативы, чтобы проверить split; вторая — ни
# одной (нет маршрута, есть интразональная ходьба).
JOURNEYS = [
    [
        Journey(
            origin_stop_id="s1",
            destination_stop_id="s3",
            legs=(
                JourneyLeg(kind="walk", from_id="z1", to_id="s1", duration_min=4.0, wait_min=0.0),
                JourneyLeg(kind="transit", route_id="r1", service_id="v1", from_id="s1", to_id="s3",
                           duration_min=8.0, wait_min=3.0),
                JourneyLeg(kind="walk", from_id="s3", to_id="z3", duration_min=6.0, wait_min=0.0),
            ),
            transfers=0,
            duration_min=18.0,
        ),
        Journey(
            origin_stop_id="s1",
            destination_stop_id="s4",
            legs=(
                JourneyLeg(kind="walk", from_id="z1", to_id="s1", duration_min=4.0, wait_min=0.0),
                JourneyLeg(kind="transit", route_id="r1", service_id="v1", from_id="s1", to_id="s2",
                           duration_min=4.0, wait_min=2.0),
                JourneyLeg(kind="walk", from_id="s2", to_id="s3", duration_min=5.0, wait_min=0.0),
                JourneyLeg(kind="transit", route_id="r2", service_id="v2", from_id="s3", to_id="s4",
                           duration_min=6.0, wait_min=4.0),
                JourneyLeg(kind="walk", from_id="s4", to_id="z3", duration_min=7.0, wait_min=0.0),
            ),
            transfers=1,
            duration_min=24.0,
        ),
    ],
    [],
    [
        Journey(
            origin_stop_id="s1",
            destination_stop_id="s3",
            legs=(
                JourneyLeg(kind="walk", from_id="z2", to_id="s1", duration_min=10.0, wait_min=0.0),
                JourneyLeg(kind="transit", route_id="r1", service_id="v1", from_id="s1", to_id="s3",
                           duration_min=8.0, wait_min=3.0),
                JourneyLeg(kind="walk", from_id="s3", to_id="z3", duration_min=6.0, wait_min=0.0),
            ),
            transfers=0,
            duration_min=24.0,
        ),
    ],
    [],
    [
        Journey(
            origin_stop_id="s1",
            destination_stop_id="s5",
            legs=(
                JourneyLeg(kind="walk", from_id="z1", to_id="s1", duration_min=0.0, wait_min=0.0),
                JourneyLeg(kind="transit", route_id="r5", service_id="v5", from_id="s1", to_id="s5",
                           duration_min=14.0, wait_min=2.0),
                JourneyLeg(kind="walk", from_id="s5", to_id="z4", duration_min=0.0, wait_min=0.0),
            ),
            transfers=0,
            duration_min=16.0,
        ),
    ],
    [
        Journey(
            origin_stop_id="s4",
            destination_stop_id="s1",
            legs=(
                JourneyLeg(kind="walk", from_id="z3", to_id="s4", duration_min=0.0, wait_min=0.0),
                JourneyLeg(kind="transit", route_id="r4", service_id="v4", from_id="s4", to_id="s1",
                           duration_min=12.0, wait_min=3.0),
                JourneyLeg(kind="walk", from_id="s1", to_id="z1", duration_min=0.0, wait_min=0.0),
            ),
            transfers=0,
            duration_min=15.0,
        ),
    ],
]

# Ближайшая остановка для каждой зоны. Каждая пара спроса обязана
# разрешаться в свою пару остановок, иначе подставной роутер отдаст всем
# парам один journey и ветка со split альтернатив не выполнится ни разу.
ZONE_STOPS = {"z1": "s1", "z2": "s2", "z3": "s4", "z4": "s5"}
STUB_ROUTES = {
    ("s1", "s4"): 0,  # z1→z3, две альтернативы
    ("s1", "s1"): 1,  # z1→z1, интразональная без маршрута
    ("s2", "s4"): 2,  # z2→z3, одна альтернатива
    ("s1", "s2"): 3,  # z1→z2, спрос нулевой — не вызывается
    ("s1", "s5"): 4,  # z1→z4, высадка в s5
    ("s4", "s1"): 5,  # z3→z1, посадка там, где другие высаживают
}

PROBE = """
import { assignOnce, DEFAULT_ASSIGNMENT_CONFIG, segmentCrowdingPenalties,
         blendFeedback, maxFeedbackDelta } from "./assignment-model.js";
import { DEFAULT_CHOICE_CONFIG } from "./choice.js";

const spec = JSON.parse(process.argv[2]);
const zones = new Map(spec.zones.map((z) => [z.id, {
  id: z.id, centroidX: z.x, centroidY: z.y,
  population: z.population, jobs: z.jobs,
}]));
const pairs = spec.pairs.map((p) => ({
  originZoneId: p[0], destinationZoneId: p[1], tripsPerDay: p[2], baseTimeMin: p[3],
}));
const journeys = spec.journeys.map((list) => list.map((j) => ({
  legs: j.legs.map((l) => ({
    kind: l.kind, routeId: l.routeId ?? null, serviceId: l.serviceId ?? null,
    fromId: l.fromId, toId: l.toId, durationMin: l.durationMin, waitMin: l.waitMin,
  })),
  transfers: j.transfers, durationMin: j.durationMin,
})));
const sectionCapacity = spec.capacity.map((c) => ({
  routeId: c[0], fromStopId: c[1], toStopId: c[2], capacity: c[3],
}));
const platformM = new Map(spec.platforms);
const segmentPenalty = new Map(spec.segmentPenalties);
const stopDwell = (stopId, boardings) =>
  (spec.dwellBase[stopId] ?? 0) + (spec.dwellPerPassenger[stopId] ?? 0) * boardings;

const snapshot = assignOnce({
  pairs, journeysPerPair: journeys, accessWalkMin: spec.access, egressWalkMin: spec.egress,
  zones, routeIds: spec.routeIds, stopIds: spec.stopIds, sectionCapacity, stopPlatformM: platformM,
  config: { ...DEFAULT_ASSIGNMENT_CONFIG, transitFare: spec.transitFare },
  choice: DEFAULT_CHOICE_CONFIG,
  segmentCrowdingPenalties: segmentPenalty,
  stopDwell,
  totalTripsPerDay: spec.totalTrips,
});

const runTime = new Map(spec.runTimes);
const target = segmentCrowdingPenalties(snapshot.sectionLoads, DEFAULT_ASSIGNMENT_CONFIG, runTime);
const blended = blendFeedback(segmentPenalty, target, 0.5, 0.0);

process.stdout.write(JSON.stringify({
  metrics: snapshot.metrics,
  unserved: snapshot.unserved,
  lossReasons: snapshot.lossReasons,
  routeFlows: snapshot.routeFlows,
  stopFlows: snapshot.stopFlows,
  sectionLoads: snapshot.sectionLoads,
  serviceStopBoardings: snapshot.serviceStopBoardings,
  crowdingTargets: [...target.entries()].sort(),
  crowdingBlended: [...blended.entries()].sort(),
  delta: maxFeedbackDelta(segmentPenalty, target),
}));
"""


def _build_network() -> Network:
    return Network(
        stops={s.id: s for s in STOPS},
        routes={r.id: r for r in ROUTES},
        periods=PERIODS,
        services={s.id: s for s in SERVICES},
        vehicle_types={v.id: v for v in VEHICLE_TYPES},
    )


def _access_egress_python(
    network: Network, pairs, zone_stops, walking_speed_kph=None
):
    """Ходьба зона→остановка ровно так, как её считает эталонный _assign_once.

    Значения нельзя задавать произвольно: при неверном подходе транзитная
    полезность уезжает на десятки минут и mode split становится неразличимым
    от нуля с обеих сторон, а расхождение теряется.
    """
    from transit_planner.assignment import _walk_minutes
    from transit_planner.game_rules import WALKING_SPEED_KPH

    if walking_speed_kph is None:
        walking_speed_kph = WALKING_SPEED_KPH

    access: list[float] = []
    egress: list[float] = []
    for pair in pairs:
        origin = zone_stops.get(pair.origin_zone_id)
        destination = zone_stops.get(pair.destination_zone_id)
        access.append(_walk_minutes(
            ZONES.get(pair.origin_zone_id),
            network.stops[origin].location if origin in network.stops else None,
            walking_speed_kph,
        ))
        egress.append(_walk_minutes(
            ZONES.get(pair.destination_zone_id),
            network.stops[destination].location if destination in network.stops else None,
            walking_speed_kph,
        ))
    return access, egress


def _capacity_python(network: Network):
    """Вместимости секций и длины платформ из эталона, без подмен."""
    from transit_planner.assignment import _section_capacity_and_platforms

    return _section_capacity_and_platforms(network, "am")


def _dwell_coeffs_python(network: Network, period_id: str = "am"):
    """Базовая стоимость стоянки и слагаемое на пассажира по остановкам.

    Эталонный _stop_dwell_seconds линейна по числу посадок, поэтому стенд
    получает ровно эти два коэффициента и восстанавливает то же значение.
    """
    from transit_planner.reference_model import REFERENCE_MODE_PROFILES

    base: dict[str, float] = {}
    per: dict[str, float] = {}
    for service in network.services.values():
        if service.headway_by_period.get(period_id) is None:
            continue
        route = network.routes[service.route_id]
        profile = REFERENCE_MODE_PROFILES[route.mode.value]
        departures = network.service_departures(service, period_id)
        for stop_id in route.stop_ids:
            base[stop_id] = base.get(stop_id, 0.0) + departures * profile.dwell_s
            per[stop_id] = per.get(stop_id, 0.0) + profile.dwell_per_passenger_s
    return base, per


def _run_times_python() -> dict[tuple[str, str, str], float]:
    network = _build_network()
    times: dict[tuple[str, str, str], float] = {}
    for route in ROUTES:
        pairs = route.segment_pairs()
        for index in range(len(pairs)):
            times[(route.id, *pairs[index])] = network.route_segment_run_time_min(route, index)
    return times


def _ts_snapshot(tmp_path: Path) -> dict | None:
    """Собирает стенд и считает assignment-model.ts на эталонных входах.

    Вместимости секций и времена в пути берутся из Python и подаются в стенд:
    их порт впереди (сеть, тиражи, интервалы), а сравнивается сам расчёт
    потоков. Иначе расхождение пришлось бы ловить по всей цепочке сразу.
    """
    node = shutil.which("node")
    if node is None or not TSC_JS.exists():
        return None
    build = subprocess.run(
        [
            node, str(TSC_JS), str(SRC / "assignment-model.ts"), str(SRC / "choice.ts"),
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
    compiled = tmp_path / "assignment-model.js"
    compiled.write_text(
        compiled.read_text(encoding="utf-8").replace('from "./choice"', 'from "./choice.js"'),
        encoding="utf-8",
    )
    (tmp_path / "probe.mjs").write_text(PROBE, encoding="utf-8")
    network = _build_network()
    capacity, platform = _capacity_python(network)
    dwell_base, dwell_per = _dwell_coeffs_python(network)
    access, egress = _access_egress_python(network, PAIRS, ZONE_STOPS)
    payload = {
        "zones": [
            {"id": z.id, "x": z.centroid_x, "y": z.centroid_y,
             "population": z.population, "jobs": z.jobs}
            for z in ZONES.values()
        ],
        "pairs": [[p.origin_zone_id, p.destination_zone_id, p.trips_per_day, p.base_time_min] for p in PAIRS],
        "journeys": [
            [
                {
                    "legs": [
                        {
                            "kind": leg.kind, "routeId": leg.route_id, "serviceId": leg.service_id,
                            "fromId": leg.from_id, "toId": leg.to_id,
                            "durationMin": leg.duration_min, "waitMin": leg.wait_min,
                        }
                        for leg in journey.legs
                    ],
                    "transfers": journey.transfers, "durationMin": journey.duration_min,
                }
                for journey in group
            ]
            for group in JOURNEYS
        ],
        "access": access, "egress": egress,
        "routeIds": [r.id for r in ROUTES], "stopIds": [s.id for s in STOPS],
        "platforms": sorted(platform.items()),
        "capacity": [list(c) for c in capacity],
        "segmentPenalties": [],
        "dwellBase": dwell_base,
        "dwellPerPassenger": dwell_per,
        "transitFare": 0.0,
        "totalTrips": sum(p.trips_per_day for p in PAIRS),
        "runTimes": [
            [f"{route}|{frm}|{to}", value]
            for (route, frm, to), value in sorted(_run_times_python().items())
        ],
    }
    run = subprocess.run(
        [node, str(tmp_path / "probe.mjs"), json.dumps(payload)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(tmp_path),
    )
    if run.returncode != 0:
        pytest.fail(f"стенд assignment не отработал:\n{run.stdout}\n{run.stderr}")
    return json.loads(run.stdout)


@pytest.mark.skipif(
    not TSC_JS.exists() or shutil.which("node") is None,
    reason="node или typescript не установлены: TS-паритет не проверяется",
)
def test_assign_once_matches_python(tmp_path: Path) -> None:
    ts = _ts_snapshot(tmp_path)
    if ts is None:
        pytest.skip("tsc недоступен")

    config = AssignmentConfig(period_id="am")
    network = _build_network()
    demand = DemandMatrix(tuple(PAIRS))
    # Каждая пара спроса обязана разрешаться в СВОЮ пару остановок, иначе
    # подставной роутер отдаст всем парам один и тот же journey и ветка со
    # split альтернатив не выполнится ни разу.
    zone_stops = ZONE_STOPS

    class _FixedRouter:
        def shortest_alternatives(self, origin, destination, **kwargs):
            index = STUB_ROUTES.get((origin.id, destination.id))
            if index is None:
                return ()
            return JOURNEYS[index]

    snapshot = _assign_once(
        network, demand, _FixedRouter(), config, ZONES, zone_stops, {}, {},
    )

    metrics = ts["metrics"]
    mismatches: list[str] = []
    # TS использует camelCase, эталон — snake_case; сверка идёт по общему ключу.
    camel_to_snake = {
        "totalTrips": "total_trips",
        "transitTrips": "transit_trips",
        "carTrips": "car_trips",
        "walkTrips": "walk_trips",
        "transitShare": "transit_share",
        "averageTransitTimeMin": "average_transit_time_min",
        "averageTransfers": "average_transfers",
        "averageWaitTimeMin": "average_wait_time_min",

        "deniedBoardings": "denied_boardings",
    }
    assert set(metrics) == set(camel_to_snake), (
        f"состав метрик разошёлся: TS {sorted(metrics)}"
    )
    expected_metrics = {
        "total_trips": snapshot.metrics.total_trips,
        "transit_trips": snapshot.metrics.transit_trips,
        "car_trips": snapshot.metrics.car_trips,
        "walk_trips": snapshot.metrics.walk_trips,
        "transit_share": snapshot.metrics.transit_share,
        "average_transit_time_min": snapshot.metrics.average_transit_time_min,
        "average_transfers": snapshot.metrics.average_transfers,
        "average_wait_time_min": snapshot.metrics.average_wait_time_min,
        "denied_boardings": snapshot.metrics.denied_boardings,
    }
    for snake, value in expected_metrics.items():
        key = next(k for k, v in camel_to_snake.items() if v == snake)
        left = float(metrics[key])
        if abs(left - value) > TOLERANCE * max(1.0, abs(value)):
            mismatches.append(f"metrics/{snake}: TS {left} != Python {value}")
    assert not mismatches, "расхождение Python и TS:\n  " + "\n  ".join(mismatches)

    assert abs(float(ts["unserved"]) - snapshot.unserved) <= TOLERANCE, (
        f"unserved: TS {ts['unserved']} != Python {snapshot.unserved}"
    )

    assert len(ts["lossReasons"]) == len(snapshot.loss_reasons), (
        f"loss_reasons: TS {ts['lossReasons']} != Python {snapshot.loss_reasons}"
    )
    for actual, loss in zip(ts["lossReasons"], snapshot.loss_reasons, strict=True):
        assert actual["reason"] == loss.reason
        assert abs(actual["trips"] - loss.trips) <= TOLERANCE

    assert len(ts["routeFlows"]) == len(snapshot.route_flows) == len(ROUTES)
    for actual, expected in zip(ts["routeFlows"], snapshot.route_flows, strict=True):
        assert actual["routeId"] == expected.route_id
        assert abs(actual["boardings"] - expected.boardings) <= TOLERANCE
        assert abs(actual["passengerSectionTraversals"] - expected.passenger_section_traversals) <= TOLERANCE

    assert len(ts["stopFlows"]) == len(snapshot.stop_flows) == len(STOPS)
    for actual, expected in zip(ts["stopFlows"], snapshot.stop_flows, strict=True):
        assert actual["stopId"] == expected.stop_id
        assert abs(actual["boardings"] - expected.boardings) <= TOLERANCE
        assert abs(actual["alightings"] - expected.alightings) <= TOLERANCE
        assert abs(actual["transfers"] - expected.transfers) <= TOLERANCE
        assert abs(actual["platformM"] - expected.platform_m) <= TOLERANCE

    assert len(ts["sectionLoads"]) == len(snapshot.section_loads)
    for actual, expected in zip(ts["sectionLoads"], snapshot.section_loads, strict=True):
        assert (actual["routeId"], actual["fromStopId"], actual["toStopId"]) == (
            expected.route_id, expected.from_stop_id, expected.to_stop_id
        )
        assert abs(actual["passengers"] - expected.passengers) <= TOLERANCE
        assert abs(actual["capacity"] - expected.capacity) <= TOLERANCE
        assert abs(actual["deniedBoardings"] - expected.denied_boardings) <= TOLERANCE
        assert abs(actual["loadRatio"] - expected.load_ratio) <= TOLERANCE
        assert actual["crowdingLevel"] == expected.crowding_level

    assert len(ts["serviceStopBoardings"]) == len(snapshot.service_stop_boardings)
    for actual, expected in zip(ts["serviceStopBoardings"], snapshot.service_stop_boardings, strict=True):
        assert (actual[0], actual[1]) == (expected[0], expected[1])
        assert abs(actual[2] - expected[2]) <= TOLERANCE

    # Обратная связь: целевые штрафы, их смешивание с damping и величина
    # изменения. Без этих проверок замена смешивания на прямую подстановку
    # осталась бы незамеченной — итоговые метрики на коротком прогоне те же.
    network_for_feedback = _build_network()
    config = AssignmentConfig(period_id="am")
    sections = _assign_once(
        network_for_feedback, demand, _FixedRouter(), config, ZONES, ZONE_STOPS, {}, {},
    ).section_loads
    target = _segment_crowding_penalties(network_for_feedback, sections, config)
    blended = {
        key: 0.0 * config.damping + value * (1.0 - config.damping)
        for key, value in target.items()
    }
    delta = _max_penalty_delta({}, target)
    for key, value in target.items():
        joined = "|".join(key)
        assert any(
            actual[0] == joined and abs(actual[1] - value) <= TOLERANCE
            for actual in ts["crowdingTargets"]
        ), f"целевой штраф {key} не совпал: {ts['crowdingTargets']}"
        assert any(
            actual[0] == joined and abs(actual[1] - blended[key]) <= TOLERANCE
            for actual in ts["crowdingBlended"]
        ), f"смешанный штраф {key} не совпал: {ts['crowdingBlended']}"
    assert abs(ts["delta"] - delta) <= TOLERANCE, (
        f"величина изменения {ts['delta']} != {delta}"
    )


def test_strict_fit_boarding_reports_denied() -> None:
    """Ограниченная вместимость обязана дать отказ в посадке, а не тишину."""
    config = AssignmentConfig(period_id="am")
    network = _build_network()
    demand = DemandMatrix((ODPairDemand("z1", "z3", 1200.0, base_time_min=32.0),))
    router = TransitRouter(network, config=RouterConfig())
    snapshot = _assign_once(
        network, demand, router, config, ZONES, {"z1": "s1", "z3": "s4"}, {}, {},
    )
    assert snapshot.metrics.denied_boardings > 0.0
    assert any(section.denied_boardings > 0.0 for section in snapshot.section_loads)


def test_crowding_feedback_damping_and_convergence() -> None:
    """Штрафы за переполненность накапливаются и сходятся по damping."""
    config = AssignmentConfig(period_id="am")
    network = _build_network()
    run_times = _run_times_python()
    sections = (
        SectionLoad("r1", "s1", "s3", passengers=180.0, capacity=100.0),
    )
    # Сегмент r1|s1|s3 отсутствует в run_times, поэтому штраф не начисляется.
    assert _segment_crowding_penalties(network, sections, config) == {}
    # А существующий сегмент даёт штраф, пропорциональный времени в пути.
    runnable = (
        SectionLoad("r1", "s1", "s2", passengers=180.0, capacity=100.0),
    )
    penalties = _segment_crowding_penalties(network, runnable, config)
    assert penalties
    base = run_times[("r1", "s1", "s2")]
    from transit_planner.reference_model import crowding_time_multiplier

    assert penalties[("r1", "s1", "s2")] == pytest.approx(
        base * (crowding_time_multiplier(1.8) - 1.0)
    )
    assert _max_penalty_delta({}, penalties) == pytest.approx(max(penalties.values()))


def test_zone_walk_and_distance_helpers() -> None:
    """Геометрия ходьбы и расстояний между зонами совпадает с эталоном."""
    from transit_planner.assignment import _distance_between_zones, _walk_minutes

    pair = ODPairDemand("z1", "z3", 10.0, base_time_min=1.0)
    assert _distance_between_zones(pair, ZONES) == pytest.approx(3000.0)
    # Пара с одинаковой зоной без данных даёт 0, разные неизвестные — 1000.
    assert _distance_between_zones(ODPairDemand("zz", "zz", 1.0, base_time_min=1.0), ZONES) == 0.0
    assert _distance_between_zones(ODPairDemand("zz", "yy", 1.0, base_time_min=1.0), ZONES) == 1000.0
    assert _walk_minutes(ZONES["z1"], STOPS[0].location, 5.0) == pytest.approx(0.0)
    assert _walk_minutes(None, STOPS[0].location, 5.0) == 0.0
    assert _walk_minutes(ZONES["z1"], None, 5.0) == 0.0
    assert _walk_minutes(ZONES["z1"], STOPS[2].location, 5.0) == pytest.approx(24.0)


def test_mode_profiles_match_model_json() -> None:
    """Профили режимов в TS обязаны совпадать с model.json."""
    from transit_planner.reference_model import REFERENCE_MODE_PROFILES

    model = json.loads((ROOT / "src" / "transit_planner" / "model.json").read_text(encoding="utf-8"))
    source = (SRC / "assignment-model.ts").read_text(encoding="utf-8")
    for mode, expected in REFERENCE_MODE_PROFILES.items():
        payload = model["modes"][mode]
        for field, ts_field, key in (
            ("capacity", "capacity", "capacity"),
            ("dwell_s", "dwellS", "dwell_s"),
            ("dwell_per_passenger_s", "dwellPerPassengerS", "dwell_per_passenger_s"),
            ("jitter_s", "jitterS", "jitter_s"),
            ("track_capacity_per_hour", "trackCapacityPerHour", "track_capacity_per_hour"),
            ("access_m", "accessM", "access_m"),
            ("platform_m", "platformM", "platform_m"),
        ):
            value = getattr(expected, field)
            assert value == pytest.approx(float(payload[key]))
            assert f"{ts_field}: {value:g}" in source or f"{ts_field}: {value}" in source, (
                f"{mode}.{ts_field} в assignment-model.ts != model.json ({value})"
            )
    for name, value in (("CROWDED_LOAD_RATIO", 1.0), ("SEVERE_LOAD_RATIO", 2.0), ("EXTREME_LOAD_RATIO", 4.0)):
        assert f"{name} = {value:g};" in source or f"{name} = {value};" in source
    from transit_planner.reference_model import (
        CROWDED_LOAD_RATIO, EXTREME_LOAD_RATIO, SEVERE_LOAD_RATIO,
    )

    assert (CROWDED_LOAD_RATIO, SEVERE_LOAD_RATIO, EXTREME_LOAD_RATIO) == (1.0, 2.0, 4.0)
