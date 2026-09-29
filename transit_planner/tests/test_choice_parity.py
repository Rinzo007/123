"""Паритет mode choice: TS choice.ts ↔ Python choice.py.

Python — эталон. Стенд собирает настоящий choice.ts локальным tsc и
прогоняет тот же набор сценариев, что и эталонный расчёт: utilities,
multinomial logit probabilities и split транзитного спроса по
альтернативам. Дополнительно константы choice.ts сверяются с model.json,
чтобы правка модели не осталась незамеченной.

Режимов ровно три: transit, car, walk. Велосипеда, доли без автомобиля и
`rest` в игре нет, поэтому в сценариях их тоже нет.

Модуль choice.ts не тянет внешних импортов, поэтому emitted JS
самодостаточен. Без node/typescript тест пропускается.
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

from transit_planner.choice import (  # noqa: E402
    ChoiceConfig,
    alternative_probabilities,
    probabilities,
    utilities,
)

SRC = ROOT / "frontend" / "src"
TSC_JS = ROOT / "frontend" / "node_modules" / "typescript" / "lib" / "tsc.js"

# Режимы: walk/car/transit. Сценарии намеренно покрывают границы: нулевые
# расстояния, недоступный транзит, платный проезд, пересадки, короткие поездки
# под штраф за неудобство и верхнюю границу пересадок игры.
SCENARIOS = [
    {
        "name": "base",
        "walk_time_min": 30.0, "car_time_min": 12.0, "transit_time_min": 18.0,
        "transit_wait_min": 4.0, "transit_fare": 0.0,
        "car_distance_km": 6.0, "transit_access_walk_min": 5.0, "transit_egress_walk_min": 6.0,
        "transit_transfer_walk_min": 3.0, "transit_transfers": 1,
    },
    {
        "name": "no_transit",
        "walk_time_min": 20.0, "car_time_min": 15.0, "transit_time_min": None,
        "transit_wait_min": 0.0, "transit_fare": 0.0,
        "car_distance_km": 3.0, "transit_access_walk_min": 0.0, "transit_egress_walk_min": 0.0,
        "transit_transfer_walk_min": 0.0, "transit_transfers": 0,
    },
    {
        "name": "paid_transit",
        "walk_time_min": 10.0, "car_time_min": 8.0, "transit_time_min": 11.0,
        "transit_wait_min": 2.0, "transit_fare": 1.2,
        "car_distance_km": 2.5, "transit_access_walk_min": 2.0, "transit_egress_walk_min": 2.0,
        "transit_transfer_walk_min": 1.0, "transit_transfers": 2,
    },
    {
        "name": "intrazonal",
        "walk_time_min": 0.0, "car_time_min": 0.0, "transit_time_min": 0.0,
        "transit_wait_min": 0.0, "transit_fare": 0.0,
        "car_distance_km": 0.0, "transit_access_walk_min": 0.0, "transit_egress_walk_min": 0.0,
        "transit_transfer_walk_min": 0.0, "transit_transfers": 0,
    },
    {
        "name": "long_trip",
        "walk_time_min": 120.0, "car_time_min": 40.0, "transit_time_min": 70.0,
        "transit_wait_min": 9.0, "transit_fare": 2.5,
        "car_distance_km": 18.0, "transit_access_walk_min": 8.0, "transit_egress_walk_min": 9.0,
        "transit_transfer_walk_min": 4.0, "transit_transfers": 3,
    },
    {
        "name": "no_wait",
        "walk_time_min": 25.0, "car_time_min": 11.0, "transit_time_min": 19.0,
        "transit_wait_min": 5.0, "transit_fare": 0.0,
        "car_distance_km": 5.0, "transit_access_walk_min": 4.0, "transit_egress_walk_min": 4.0,
        "transit_transfer_walk_min": 2.0, "transit_transfers": 0,
    },
    {
        # Короткая поездка попадает под штраф за неудобство (< 1 км).
        "name": "short_car_trip",
        "walk_time_min": 14.0, "car_time_min": 7.0, "transit_time_min": 16.0,
        "transit_wait_min": 3.0, "transit_fare": 0.0,
        "car_distance_km": 0.35, "transit_access_walk_min": 3.0, "transit_egress_walk_min": 3.0,
        "transit_transfer_walk_min": 2.0, "transit_transfers": 0,
    },
    {
        # Четыре пересадки - верхняя граница игры; запас 50 с на каждую.
        "name": "max_transfers",
        "walk_time_min": 35.0, "car_time_min": 26.0, "transit_time_min": 48.0,
        "transit_wait_min": 11.0, "transit_fare": 1.9,
        "car_distance_km": 21.0, "transit_access_walk_min": 7.0, "transit_egress_walk_min": 7.0,
        "transit_transfer_walk_min": 5.0, "transit_transfers": 4,
    },
    {
        # Нулевое расстояние авто: данных о поездке нет, штраф не применяется.
        "name": "car_distance_unknown",
        "walk_time_min": 40.0, "car_time_min": 9.0, "transit_time_min": 33.0,
        "transit_wait_min": 6.0, "transit_fare": 0.0,
        "car_distance_km": 0.0, "transit_access_walk_min": 4.0, "transit_egress_walk_min": 4.0,
        "transit_transfer_walk_min": 2.0, "transit_transfers": 2,
    },
]

# Несколько наборов обобщённых стоимостей для split альтернатив.
COST_SETS = [
    [30.0],
    [30.0, 45.0],
    [12.0, 20.0, 55.0, 80.0],
    [0.0, 0.5, 1.0],
    [1e6, 1e6, 1e6],
]

TOLERANCE = 1e-12

PROBE = """
import { utilities, probabilities, alternativeProbabilities } from "./choice.js";

// JSON не умеет -Infinity (становится null), поэтому кодируем строкой.
// Реальный воркер передаёт результат через structured clone, где -Infinity
// выживает, - это ограничение только тестового стенда.
const encode = (value) => (value === -Infinity ? "-inf" : value);

const spec = JSON.parse(process.argv[2]);
const out = spec.scenarios.map((s) => {
  const u = utilities({
    walkTimeMin: s.walk_time_min,
    carTimeMin: s.car_time_min,
    transitTimeMin: s.transit_time_min,
    transitWaitMin: s.transit_wait_min,
    transitFare: s.transit_fare,
    carDistanceKm: s.car_distance_km,
    transitAccessWalkMin: s.transit_access_walk_min,
    transitEgressWalkMin: s.transit_egress_walk_min,
    transitTransferWalkMin: s.transit_transfer_walk_min,
    transitTransfers: s.transit_transfers,
  });
  return {
    utilities: {
      walk: encode(u.walk), car: encode(u.car), transit: encode(u.transit),
    },
    probabilities: (() => {
      const p = probabilities(u);
      return { transit: p.transit, car: p.car, walk: p.walk };
    })(),
  };
});
process.stdout.write(JSON.stringify({
  cases: out,
  alternatives: spec.costSets.map(alternativeProbabilities),
}));
"""


def _ts_out(tmp_path: Path) -> dict | None:
    node = shutil.which("node")
    if node is None or not TSC_JS.exists():
        return None
    build = subprocess.run(
        [
            node, str(TSC_JS), str(SRC / "choice.ts"), str(SRC / "game-rules.ts"),
            "--outDir", str(tmp_path),
            "--rootDir", str(SRC),
            "--module", "esnext",
            "--target", "es2022",
            "--ignoreConfig",
        ],
        capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT / "frontend"),
    )
    if build.returncode != 0:
        pytest.fail(f"tsc не собрал choice.ts:\n{build.stdout}\n{build.stderr}")
    (tmp_path / "probe.mjs").write_text(PROBE, encoding="utf-8")
    run = subprocess.run(
        [
            node, str(tmp_path / "probe.mjs"),
            json.dumps({"scenarios": SCENARIOS, "costSets": COST_SETS}),
        ],
        capture_output=True, text=True, encoding="utf-8", cwd=str(tmp_path),
    )
    if run.returncode != 0:
        pytest.fail(f"стенд choice не отработал:\n{run.stdout}\n{run.stderr}")
    return json.loads(run.stdout)


def _python_out() -> dict:
    config = ChoiceConfig()
    cases = []
    for scenario in SCENARIOS:
        u = utilities(
            walk_time_min=scenario["walk_time_min"],
            car_time_min=scenario["car_time_min"],
            transit_time_min=scenario["transit_time_min"],
            transit_wait_min=scenario["transit_wait_min"],
            transit_fare=scenario["transit_fare"],
            car_distance_km=scenario["car_distance_km"],
            transit_access_walk_min=scenario["transit_access_walk_min"],
            transit_egress_walk_min=scenario["transit_egress_walk_min"],
            transit_transfer_walk_min=scenario["transit_transfer_walk_min"],
            transit_transfers=scenario["transit_transfers"],
            config=config,
        )
        cases.append({
            "utilities": {"walk": u.walk, "car": u.car, "transit": u.transit},
            "probabilities": probabilities(u),
        })
    return {
        "cases": cases,
        "alternatives": [list(alternative_probabilities(tuple(costs))) for costs in COST_SETS],
    }


@pytest.mark.skipif(
    not TSC_JS.exists() or shutil.which("node") is None,
    reason="node или typescript не установлены: TS-паритет не проверяется",
)
def test_mode_choice_matches_python(tmp_path: Path) -> None:
    ts = _ts_out(tmp_path)
    if ts is None:
        pytest.skip("tsc недоступен")
    expected = _python_out()
    assert len(ts["cases"]) == len(expected["cases"]) == len(SCENARIOS)

    for scenario, actual, reference in zip(SCENARIOS, ts["cases"], expected["cases"], strict=True):
        name = scenario["name"]
        for mode in ("walk", "car", "transit"):
            raw = actual["utilities"][mode]
            left = float("-inf") if raw == "-inf" else float(raw)
            right = float(reference["utilities"][mode])
            if right == float("-inf"):
                assert left == float("-inf"), f"{name}/{mode}: TS {left} != Python -inf"
            else:
                assert abs(left - right) <= TOLERANCE * max(1.0, abs(right)), (
                    f"{name}/{mode} utility: TS {left} != Python {right}"
                )
        left_probs = actual["probabilities"]
        right_probs = reference["probabilities"]
        assert set(left_probs) == {"transit", "car", "walk"}
        for mode in ("transit", "car", "walk"):
            left = float(left_probs[mode])
            assert abs(left - float(right_probs[mode])) <= TOLERANCE, (
                f"{name}/{mode}: TS {left_probs[mode]} != Python {right_probs[mode]}"
            )
        total = sum(float(value) for value in left_probs.values())
        assert total == pytest.approx(1.0, abs=1e-9), (
            f"{name}: доли не дают 1, сумма {total}"
        )


@pytest.mark.skipif(
    not TSC_JS.exists() or shutil.which("node") is None,
    reason="node или typescript не установлены: TS-паритет не проверяется",
)
def test_alternative_probabilities_match_python(tmp_path: Path) -> None:
    ts = _ts_out(tmp_path)
    if ts is None:
        pytest.skip("tsc недоступен")
    expected = _python_out()["alternatives"]
    assert len(ts["alternatives"]) == len(expected)
    for costs, left, right in zip(COST_SETS, ts["alternatives"], expected, strict=True):
        assert len(left) == len(right)
        for index, (a, b) in enumerate(zip(left, right, strict=True)):
            assert abs(a - b) <= TOLERANCE, (
                f"cost set {costs}[{index}]: TS {a} != Python {b}"
            )
        if right:
            assert sum(left) == pytest.approx(1.0, abs=1e-12)


def test_choice_literals_match_model_json() -> None:
    """Константы choice.ts обязаны совпадать с эталонной моделью."""
    model = json.loads((ROOT / "src" / "transit_planner" / "model.json").read_text(encoding="utf-8"))
    source = (SRC / "choice.ts").read_text(encoding="utf-8")
    config = ChoiceConfig()
    expected = {
        "REFERENCE_VOT_S_PER_EUR": float(model["vot_s_per_eur"]),
        "REFERENCE_WAIT_WEIGHT": float(model["journey_choice"]["wait_weight"]),
        "REFERENCE_TRANSFER_RIDER_BIAS_S": float(model["transfer"]["rider_bias_s"]),
        "REFERENCE_TRANSFER_WALK_MULTIPLIER": float(model["transfer"]["walk_multiplier"]),
        "REFERENCE_TRANSIT_STAGE_ACCESS_WEIGHT": float(model["transit_burdens"]["stage_access_weight"]),
        "REFERENCE_TRANSIT_STAGE_EGRESS_WEIGHT": float(model["transit_burdens"]["stage_egress_weight"]),
        "REFERENCE_TRANSIT_STAGE_TRANSFER_WALK_WEIGHT": float(
            model["transit_burdens"]["stage_transfer_walk_weight"]
        ),
        "REFERENCE_TRANSIT_BURDEN_FIRST_MIN": float(model["transit_burdens"]["first_transfer_burden_min"]),
        "REFERENCE_TRANSIT_BURDEN_MULTIPLE_MIN": float(model["transit_burdens"]["multiple_transfer_burden_min"]),
        "REFERENCE_CAR_COST_PER_KM_EUR": float(model["car"]["cost_per_km_eur"]),
        "REFERENCE_CAR_PARKING_EUR": float(model["car"]["parking_eur"]),
        "REFERENCE_CAR_PARKING_S": float(model["car"]["parking_s"]),
        "REFERENCE_CAR_CIRCUITY": float(model["car"]["circuity"]),
    }
    for name, value in expected.items():
        assert f"{name} = {value:g};" in source or f"{name} = {value};" in source, (
            f"{name} в choice.ts не совпадает с model.json ({value})"
        )
    # Производные величины в ChoiceConfig обязаны совпадать с эталоном.
    assert config.time_coefficient == pytest.approx(60.0 / float(model["vot_s_per_eur"]))
    assert config.walk_circuity == pytest.approx(1.33 * float(model["transfer"]["walk_multiplier"]))
    assert config.car_parking_minutes == pytest.approx(float(model["car"]["parking_s"]) / 60.0)
    assert config.transit_bias_minutes == pytest.approx(float(model["transfer"]["rider_bias_s"]) / 60.0)
