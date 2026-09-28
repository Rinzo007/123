"""Паритет отбора альтернатив: TS journey-alternatives.ts ↔ Python.

Роутеры у TS и Python различаются намеренно, поэтому сравниваются правила
отбора, а не сам поиск пути: на обе стороны подаётся один и тот же подставной
роутер, который по штрафам и запрещённым маршрутам возвращает journey из
фиксированной таблицы. Так сверяются perceived time, Pareto-доминирование,
прогрессия штрафов за разнообразие, досрочная остановка цикла и порядок
выживших альтернатив.

Модуль самодостаточен, стенд собирает его одним tsc. Без node/typescript тест
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

from transit_planner.routing import (  # noqa: E402
    Journey,
    JourneyLeg,
    RouterConfig,
    TransitRouter,
    _dominates,
    pareto_filter_journeys,
)

SRC = ROOT / "frontend" / "src"
TSC_JS = ROOT / "frontend" / "node_modules" / "typescript" / "lib" / "tsc.js"
TOLERANCE = 1e-9

# Таблица подставного роутера: ключ — (штрафы, запрещённые маршруты), значение —
# journey или None («маршрутов больше нет»).
#
# Сценарий покрывает: две альтернативы с разными наборами маршрутов; третью,
# которую роутер больше не отдаёт (None) — цикл обязан остановиться; и случай
# повторной последовательности маршрутов, когда цикл тоже обязан выйти.
# Ключи строятся из penalty-состояния, поэтому несовпадение прогрессии штрафов
# немедленно ломает всю последовательность вызовов.
TABLE = {
    "none": [
        ("A", 0, 20.0, 0.0, 5.0),
    ],
    "A:15": [
        ("B", 0, 30.0, 0.0, 2.0),
    ],
    "A:15,B:30": None,
    "onlyA": [
        ("A", 0, 20.0, 0.0, 5.0),
    ],
    "onlyA,A:15": [
        ("A", 1, 25.0, 3.0, 5.0),
    ],
}

SCENARIOS = [
    # maxAlternatives=3, разнообразие 15: A → B → None.
    {"name": "three_routes", "maxAlternatives": 3, "diversity": 15.0, "repeat": False},
    # Роутер на втором проходе повторяет ту же последовательность: цикл выходит.
    {"name": "repeat_sequence", "maxAlternatives": 3, "diversity": 15.0, "repeat": True},
    # maxAlternatives=1: альтернатив ровно одна, штрафы не накапливаются.
    {"name": "single", "maxAlternatives": 1, "diversity": 15.0, "repeat": False},
    # Нулевая разница штрафов: маршрут не банится, но зацикливается на A.
    {"name": "zero_diversity", "maxAlternatives": 3, "diversity": 0.0, "repeat": False},
]

PROBE = """
import { selectAlternatives, paretoFilterJourneys, perceivedTimeMinutes, dominates }
  from "./journey-alternatives.js";

const spec = JSON.parse(process.argv[2]);

// Ключ состояния штрафов: запрещённые маршруты не входят, роутер отвечает
// только по штрафам (как в эталонном _penalty_key).
const keyOf = (penalties) => {
  const parts = [...penalties.entries()]
    .filter(([, v]) => v !== 0)
    .sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0))
    .map(([k, v]) => `${k}:${v}`);
  return parts.length === 0 ? "none" : parts.join(",");
};

// Как в эталоне: journey — это список строк (ног), берётся первая.
// buildOne возвращает один journey, а не массив, иначе map даёт вложенные
// массивы и durationMin выходит undefined.
const buildOne = (rows) => {
  const r = rows[0];
  return {
    routeIds: [r[0]],
    legs: [
      { kind: "walk", fromId: "a", toId: "b", durationMin: r[2], waitMin: 0 },
      { kind: "transit", routeId: r[0], fromId: "b", toId: "c", durationMin: r[2], waitMin: r[3] },
      { kind: "walk", fromId: "c", toId: "d", durationMin: r[4], waitMin: 0 },
    ],
    transfers: r[1], durationMin: r[2] + r[3] + r[4],
  };
};

const out = spec.scenarios.map((s) => {
  const step = { count: 0, keys: [] };
  const result = selectAlternatives({
    maxAlternatives: s.maxAlternatives,
    diversityPenaltyMin: s.diversity,
    route: (penalties) => {
      step.count += 1;
      const key = keyOf(penalties);
      step.keys.push(key);
      if (s.repeat && step.count === 2) {
        return buildOne(spec.table.none);
      }
      const rows = spec.table[key];
      if (!rows) return null;
      return buildOne(rows);
    },
  });
  return {
    keys: step.keys,
    finalKeys: [...result.finalRoutePenalties.entries()].sort(),
    banned: [...result.bannedRouteIds].sort(),
    passes: result.passes,
    journeys: result.journeys.map((j) => [j.routeIds.join("|"), j.transfers, j.durationMin]),
    perceived: result.journeys.map((j) => perceivedTimeMinutes(j)),
  };
});

const paretoCases = spec.paretoCases.map(
  (journeys) => paretoFilterJourneys(journeys.map(buildOne)).map((j) => j.durationMin),
);
const dominant = spec.dominance.map(([l, r]) => dominates(l, r));

process.stdout.write(JSON.stringify({ cases: out, paretoCases, dominant }));
"""


def _python_cases(network) -> list[dict]:
    """Прогоняет эталонный shortest_alternatives с тем же подставным роутером.

    Роутер подменяется на уровне `TransitRouter.shortest`: цикл
    `shortest_alternatives` всегда вызывает именно его, и так сравнивается вся
    последовательность состояний штрафов, а не только итоговый список journey.
    """
    router = TransitRouter(network, config=RouterConfig())
    results = []
    for scenario in SCENARIOS:
        state = {"count": 0, "keys": []}

        def stub(route_penalties, banned_route_ids):
            state["count"] += 1
            key = _penalty_key(route_penalties)
            state["keys"].append(key)
            if scenario["repeat"] and state["count"] == 2:
                return _journey_from_rows(TABLE["none"])
            rows = TABLE.get(key)
            if rows is None:
                return None
            return _journey_from_rows(rows)

        def fake_shortest(*_args, route_penalties=None, banned_route_ids=frozenset(), **_kwargs):
            return stub(route_penalties or {}, banned_route_ids)

        original = router.shortest
        router.shortest = fake_shortest
        try:
            journeys = router.shortest_alternatives(
                _stop(network, "s1"),
                _stop(network, "s3"),
                period_id="am",
                max_alternatives=scenario["maxAlternatives"],
                diversity_penalty_min=scenario["diversity"],
            )
        finally:
            router.shortest = original
        results.append({
            "keys": state["keys"],
            "journeys": [
                ("|".join(leg.route_id for leg in j.legs if leg.kind == "transit" and leg.route_id),
                 j.transfers, j.duration_min)
                for j in journeys
            ],
        })
    return results


def _penalty_key(penalties: dict[str, float]) -> str:
    """Ключ состояния штрафов для подставного роутера.

    Запрещённые маршруты в ключ не входят: роутер отвечает только по набору
    штрафов, а запреты проверяются отдельно через итоговые journey.
    """
    parts = [
        f"{key}:{value:g}"
        for key, value in sorted(penalties.items())
        if value != 0
    ]
    return ",".join(parts) if parts else "none"


def _journey_from_rows(rows) -> Journey:
    _route, transfers, in_vehicle, wait, walk = rows[0]
    legs = (
        JourneyLeg(kind="walk", from_id="a", to_id="b", duration_min=in_vehicle, wait_min=0.0),
        JourneyLeg(kind="transit", route_id=_route, from_id="b", to_id="c",
                   duration_min=in_vehicle, wait_min=wait),
        JourneyLeg(kind="walk", from_id="c", to_id="d", duration_min=walk, wait_min=0.0),
    )
    return Journey(
        origin_stop_id="s1",
        destination_stop_id="s3",
        duration_min=in_vehicle + wait + walk,
        transfers=transfers,
        legs=legs,
    )


def _stop(network, stop_id: str):
    return network.stops[stop_id]


def _build_network():
    from shapely.geometry import Point
    from transit_planner.network import (
        Network, Route, Service, ServicePeriod, Stop, TransitMode, VehicleType,
    )

    stops = {
        s: Stop(id=s, name=s, location=Point(float(i) * 1000.0, 0.0))
        for i, s in enumerate(["s1", "s2", "s3"])
    }
    routes = {
        r.id: r for r in [
            Route(id="rA", name="A", mode=TransitMode.BUS,
                  stop_ids=("s1", "s2", "s3"), both_ways=False, closed=False),
            Route(id="rB", name="B", mode=TransitMode.BUS,
                  stop_ids=("s1", "s2", "s3"), both_ways=False, closed=False),
        ]
    }
    return Network(
        stops=stops,
        routes=routes,
        vehicle_types={"vt": VehicleType(id="vt", name="Bus", mode=TransitMode.BUS, capacity=90)},
        periods={"am": ServicePeriod(id="am", start_minute=360, end_minute=540)},
        services={
            "vA": Service(id="vA", route_id="rA", vehicle_type_id="vt", headway_by_period={"am": 10}),
            "vB": Service(id="vB", route_id="rB", vehicle_type_id="vt", headway_by_period={"am": 10}),
        },
    )


# Наборы для Pareto-фильтра: дубликаты, доминирование по разным осям, равенство.
PARETO_CASES = [
    [[("A", 0, 20.0, 0.0, 5.0)]],
    [[("A", 0, 20.0, 0.0, 5.0)], [("B", 1, 30.0, 0.0, 2.0)]],
    [[("A", 0, 10.0, 0.0, 1.0)], [("B", 1, 30.0, 0.0, 2.0)]],
    [[("A", 0, 10.0, 0.0, 1.0)], [("B", 1, 20.0, 0.0, 2.0)], [("C", 0, 40.0, 0.0, 1.0)]],
    [[("A", 0, 10.0, 0.0, 1.0)], [("A", 0, 10.0, 0.0, 1.0)]],
    [[("A", 0, 20.0, 3.0, 5.0)], [("B", 0, 20.0, 0.0, 5.0)]],
]
DOMINANCE = [
    [[1.0, 1.0, 1.0], [1.0, 1.0, 1.0]],
    [[1.0, 1.0, 1.0], [2.0, 2.0, 2.0]],
    [[2.0, 2.0, 2.0], [1.0, 1.0, 1.0]],
    [[1.0, 2.0, 3.0], [1.0, 1.0, 3.0]],
]


def _ts_out(tmp_path: Path) -> dict | None:
    node = shutil.which("node")
    if node is None or not TSC_JS.exists():
        return None
    build = subprocess.run(
        [
            node, str(TSC_JS), str(SRC / "journey-alternatives.ts"),
            "--outDir", str(tmp_path),
            "--rootDir", str(SRC),
            "--module", "esnext",
            "--target", "es2022",
            "--ignoreConfig",
        ],
        capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT / "frontend"),
    )
    if build.returncode != 0:
        pytest.fail(f"tsc не собрал journey-alternatives.ts:\n{build.stdout}\n{build.stderr}")
    (tmp_path / "probe.mjs").write_text(PROBE, encoding="utf-8")
    payload = {
        "table": TABLE,
        "scenarios": SCENARIOS,
        "paretoCases": PARETO_CASES,
        "dominance": DOMINANCE,
    }
    run = subprocess.run(
        [node, str(tmp_path / "probe.mjs"), json.dumps(payload)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(tmp_path),
    )
    if run.returncode != 0:
        pytest.fail(f"стенд альтернатив не отработал:\n{run.stdout}\n{run.stderr}")
    return json.loads(run.stdout)


@pytest.mark.skipif(
    not TSC_JS.exists() or shutil.which("node") is None,
    reason="node или typescript не установлены: TS-паритет не проверяется",
)
def test_alternative_selection_matches_python(tmp_path: Path) -> None:
    ts = _ts_out(tmp_path)
    if ts is None:
        pytest.skip("tsc недоступен")
    network = _build_network()
    expected = _python_cases(network)

    for scenario, actual, reference in zip(SCENARIOS, ts["cases"], expected, strict=True):
        name = scenario["name"]
        # Последовательность состояний штрафов — главное: именно она отличает
        # правильную прогрессию (rank + 1) от других формул, дающих тот же
        # итоговый список journey.
        assert actual["keys"] == reference["keys"], (
            f"{name}: последовательность штрафов TS {actual['keys']} "
            f"!= Python {reference['keys']}"
        )
        assert len(actual["journeys"]) == len(reference["journeys"]), (
            f"{name}: число альтернатив TS {len(actual['journeys'])} "
            f"!= Python {len(reference['journeys'])}"
        )
        for left, right in zip(actual["journeys"], reference["journeys"], strict=True):
            assert left[0] == right[0], f"{name}: набор маршрутов {left[0]} != {right[0]}"
            assert left[1] == right[1], f"{name}: пересадки {left[1]} != {right[1]}"
            assert abs(left[2] - right[2]) <= TOLERANCE, (
                f"{name}: длительность {left[2]} != {right[2]}"
            )
        assert actual["passes"] == len(reference["keys"]), (
            f"{name}: число проходов TS {actual['passes']} != {len(reference['keys'])}"
        )
        assert actual["passes"] <= scenario["maxAlternatives"], (
            f"{name}: проходов {actual['passes']} больше лимита"
        )


def _perceived(journey: Journey) -> float:
    """Perceived time эталона: конфиг роутера нужен только ради весов."""
    router = TransitRouter.__new__(TransitRouter)
    router.config = RouterConfig()
    return TransitRouter._perceived_time_minutes(router, journey)


@pytest.mark.skipif(
    not TSC_JS.exists() or shutil.which("node") is None,
    reason="node или typescript не установлены: TS-паритет не проверяется",
)
def test_pareto_filter_matches_python(tmp_path: Path) -> None:
    ts = _ts_out(tmp_path)
    if ts is None:
        pytest.skip("tsc недоступен")
    for case, actual in zip(PARETO_CASES, ts["paretoCases"], strict=True):
        journeys = tuple(_journey_from_rows(rows) for rows in case)
        kept = pareto_filter_journeys(journeys, perceived_time=_perceived)
        assert [round(j.duration_min, 9) for j in kept] == [round(v, 9) for v in actual], (
            f"Pareto разошёлся на {case}: TS {actual} "
            f"!= Python {[j.duration_min for j in kept]}"
        )


def test_pareto_keeps_non_dominated_and_identical() -> None:
    """Доминируемые отбрасываются, точные дубликаты остаются все."""
    short = _journey_from_rows([("A", 0, 10.0, 0.0, 1.0)])
    # Быстрее, но с пересадкой: ни одна из сторон не доминирует.
    fast_transfer = _journey_from_rows([("C", 1, 5.0, 0.0, 1.0)])
    assert pareto_filter_journeys((short, fast_transfer), perceived_time=_perceived) == (
        short, fast_transfer,
    )
    # Доминируемый по всем осям выпадает.
    dominated = _journey_from_rows([("D", 1, 40.0, 0.0, 5.0)])
    assert pareto_filter_journeys((short, dominated), perceived_time=_perceived) == (short,)
    # Точные дубликаты не доминируют друг друга и оба выживают.
    assert len(pareto_filter_journeys((short, short), perceived_time=_perceived)) == 2


def test_dominates_requires_strict_improvement() -> None:
    """Равные векторы не доминируют: без этого дубликаты выживали бы иначе."""
    assert _dominates((1.0, 1.0, 1.0), (1.0, 1.0, 1.0)) is False
    assert _dominates((1.0, 1.0, 1.0), (2.0, 2.0, 2.0)) is True
    assert _dominates((2.0, 2.0, 2.0), (1.0, 1.0, 1.0)) is False
    assert _dominates((1.0, 2.0, 3.0), (1.0, 1.0, 3.0)) is False
    # Лучше по одной оси, но хуже по другой — доминирования нет.
    assert _dominates((1.0, 1.0, 3.0), (1.0, 2.0, 3.0)) is True
    # Лучше ровно по одной оси, не хуже по остальным — доминирует.
    assert _dominates((1.0, 1.0, 1.0), (1.0, 1.0, 2.0)) is True


def test_shortest_alternatives_respects_max_and_bans_routes() -> None:
    """Эталон: запрещённые маршруты не используются, дубли не возвращаются."""
    network = _build_network()
    router = TransitRouter(network, config=RouterConfig())
    journeys = router.shortest_alternatives(
        _stop(network, "s1"), _stop(network, "s3"), period_id="am", max_alternatives=2
    )
    assert 1 <= len(journeys) <= 2
    sequences = [
        tuple(leg.route_id for leg in j.legs if leg.kind == "transit" and leg.route_id)
        for j in journeys
    ]
    assert len(set(sequences)) == len(sequences), "последовательности маршрутов повторились"


def test_shortest_alternatives_rejects_bad_arguments() -> None:
    network = _build_network()
    router = TransitRouter(network, config=RouterConfig())
    assert router.shortest_alternatives(
        _stop(network, "s1"), _stop(network, "s3"), period_id="am", max_alternatives=0
    ) == ()
    with pytest.raises(ValueError):
        router.shortest_alternatives(
            _stop(network, "s1"), _stop(network, "s3"), period_id="am", diversity_penalty_min=-1.0
        )
