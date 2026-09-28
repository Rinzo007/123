"""Паритет сглаживания парка на границах периодов (Python + TS).

Python здесь эталон (analytics.fleet_required_at_minute). Проверка собирает
настоящий planning/fleet.ts локальным tsc и сверяет значения. Модуль не имеет
value-импортов, поэтому emitted JS самодостаточен. Без node/typescript —
пропуск.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from math import ceil
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from transit_planner.analytics import fleet_required_at_minute  # noqa: E402
from transit_planner.network import ServicePeriod  # noqa: E402

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
SRC = FRONTEND / "src"
TS_MODULE = SRC / "planning" / "fleet.ts"
TSC_JS = FRONTEND / "node_modules" / "typescript" / "lib" / "tsc.js"

PERIODS = (
    ServicePeriod("am", 360, 540),
    ServicePeriod("pm", 540, 720),
)
HEADWAYS = {"am": 10.0, "pm": 20.0}
CYCLE = 60.0

PROBE = """
import { fleetRequiredAt } from "./planning/fleet.js";

const spec = JSON.parse(process.argv[2]);
const out = spec.minutes.map((minute) =>
  fleetRequiredAt(spec.periods, spec.headways, spec.cycle, minute),
);
process.stdout.write(JSON.stringify(out));
"""


def _minutes() -> list[float]:
    return [360.0, 400.0, 500.0, 539.0, 540.0, 541.0, 570.0, 600.0, 719.0, 720.0, 300.0, 800.0]


def _python_answers() -> list[float]:
    return [
        fleet_required_at_minute(PERIODS, HEADWAYS, CYCLE, minute) for minute in _minutes()
    ]


def _ts_answers(tmp_path: Path) -> list[float] | None:
    node = shutil.which("node")
    if node is None or not TSC_JS.exists():
        return None
    result = subprocess.run(
        [
            node,
            str(TSC_JS),
            str(TS_MODULE),
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
    (tmp_path / "probe.mjs").write_text(PROBE, encoding="utf-8")
    payload = json.dumps(
        {
            "periods": [
                {"id": p.id, "start_minute": p.start_minute, "end_minute": p.end_minute}
                for p in PERIODS
            ],
            "headways": HEADWAYS,
            "cycle": CYCLE,
            "minutes": _minutes(),
        }
    )
    run = subprocess.run(
        [node, str(tmp_path / "probe.mjs"), payload],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=tmp_path,
    )
    if run.returncode != 0:
        pytest.fail(f"стенд не отработал:\n{run.stdout}\n{run.stderr}")
    return json.loads(run.stdout)


def test_blended_fleet_mid_period_is_integer_requirement() -> None:
    assert fleet_required_at_minute(PERIODS, HEADWAYS, CYCLE, 480.0) == ceil(60.0 / 10.0)
    assert fleet_required_at_minute(PERIODS, HEADWAYS, CYCLE, 660.0) == ceil(60.0 / 20.0)


def test_blended_fleet_at_boundary_is_mean() -> None:
    assert fleet_required_at_minute(PERIODS, HEADWAYS, CYCLE, 540.0) == pytest.approx(
        (ceil(60.0 / 10.0) + ceil(60.0 / 20.0)) / 2.0
    )


def test_blended_fleet_ramps_linearly() -> None:
    before = fleet_required_at_minute(PERIODS, HEADWAYS, CYCLE, 510.0)
    middle = fleet_required_at_minute(PERIODS, HEADWAYS, CYCLE, 540.0)
    after = fleet_required_at_minute(PERIODS, HEADWAYS, CYCLE, 570.0)
    assert before > middle > after
    assert middle - after == pytest.approx(before - middle)


def test_blended_fleet_outside_periods_is_zero() -> None:
    assert fleet_required_at_minute(PERIODS, HEADWAYS, CYCLE, 300.0) == 0.0
    assert fleet_required_at_minute(PERIODS, HEADWAYS, CYCLE, 800.0) == 0.0


def test_blended_fleet_rejects_bad_inputs() -> None:
    with pytest.raises(ValueError):
        fleet_required_at_minute(PERIODS, HEADWAYS, 0.0, 400.0)
    with pytest.raises(ValueError):
        fleet_required_at_minute(PERIODS, HEADWAYS, CYCLE, float("inf"))


@pytest.mark.skipif(
    not TSC_JS.exists() or shutil.which("node") is None,
    reason="node или typescript не установлены: TS-паритет не проверяется",
)
def test_fleet_blending_matches_typescript(tmp_path: Path) -> None:
    ts = _ts_answers(tmp_path)
    if ts is None:
        pytest.skip("tsc недоступен")
    for actual, expected, minute in zip(ts, _python_answers(), _minutes(), strict=True):
        assert actual == pytest.approx(expected), f"минута {minute}: TS {actual} != Python {expected}"
