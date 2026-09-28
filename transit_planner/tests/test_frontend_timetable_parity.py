"""Паритет формулы первого отправления между TS и timetable.py.

Python здесь эталон. Проверка запускает настоящий planning/timetable.ts,
собранный локальным tsc, поэтому расхождение остатков JS и Python не может
просочиться незамеченным. Если node или typescript недоступны, тест пропускается.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from transit_planner.timetable import generate_service_timetable  # noqa: E402

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
TS_MODULE = FRONTEND / "src" / "planning" / "timetable.ts"
TSC_JS = FRONTEND / "node_modules" / "typescript" / "lib" / "tsc.js"

# start, end, offset, headway: подобраны так, чтобы старые реализации
# (JS-остаток и start+offset) расходились с Python.
CASES = [
    (360, 540, 0, 10),
    (360, 540, 15, 10),
    (360, 540, 0, 7),
    (360, 540, 25, 7),
    (360, 540, 95, 20),
    (360, 540, 200, 90),
    (240, 360, 100, 45),
    (900, 1140, 0, 181),
    (1440 - 120, 1440, 119, 7),
]

PROBE = """
import { generateDepartures } from "./timetable.js";

const cases = JSON.parse(process.argv[2]);
const out = cases.map(([start, end, offset, headway]) =>
  generateDepartures({ id: "p", start_minute: start, end_minute: end }, headway, offset).departures_minute,
);
process.stdout.write(JSON.stringify(out));
"""


def _ts_departures(tmp_path: Path) -> list[list[float]] | None:
    node = shutil.which("node")
    if node is None or not TSC_JS.exists():
        return None
    # Компилируется только сам модуль: probe написан обычным JS, поэтому
    # tsc не обязан искать общий rootDir для файлов из разных деревьев.
    result = subprocess.run(
        [
            node,
            str(TSC_JS),
            str(TS_MODULE),
            "--outDir",
            str(tmp_path),
            "--rootDir",
            str(TS_MODULE.parents[1]),
            "--module",
            "esnext",
            "--target",
            "es2022",
            "--ignoreConfig",
        ],
        capture_output=True,
        text=True,
        cwd=FRONTEND,
    )
    if result.returncode != 0:
        pytest.fail(f"tsc не собрал паритетный стенд:\n{result.stdout}\n{result.stderr}")
    # tsc сохраняет структуру относительно rootDir (src).
    (tmp_path / "probe.mjs").write_text(
        PROBE.replace("./timetable.js", "./planning/timetable.js"), encoding="utf-8"
    )
    payload = json.dumps(CASES)
    run = subprocess.run(
        [node, str(tmp_path / "probe.mjs"), payload],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    if run.returncode != 0:
        pytest.fail(f"паритетный стенд не отработал:\n{run.stdout}\n{run.stderr}")
    return json.loads(run.stdout)


def test_routing_worker_reuses_shared_departure_formula() -> None:
    """workers/routing.ts должен брать формулу из planning/timetable.ts.

    Раньше формула отправлений жила в двух местах и разошлась с Python:
    JS-остаток давал отправление до начала периода. Структурная проверка
    не даст реализациям снова разойтись.
    """
    source = (FRONTEND / "src" / "workers" / "routing.ts").read_text(encoding="utf-8")
    assert "firstDepartureMinute" in source, (
        "workers/routing.ts обязан использовать firstDepartureMinute из planning/timetable.ts"
    )
    assert "%" not in source, (
        "в workers/routing.ts не должно быть собственной арифметики остатка"
    )


@pytest.mark.skipif(
    not TSC_JS.exists() or shutil.which("node") is None,
    reason="node или typescript не установлены: TS-паритет не проверяется",
)
def test_ts_departure_formula_matches_python(tmp_path: Path) -> None:
    observed = _ts_departures(tmp_path)
    if observed is None:
        pytest.skip("tsc недоступен")
    for (start, end, offset, headway), actual in zip(CASES, observed, strict=True):
        expected = list(
            generate_service_timetable(
                service_id="s",
                period_windows={"p": (start, end)},
                headway_by_period={"p": headway},
                offset_minute=offset,
            ).periods[0].departures_minute
        )
        assert actual == expected, (
            f"период {start}-{end}, offset={offset}, headway={headway}: "
            f"TS {actual} != Python {expected}"
        )
        assert all(start <= minute < end for minute in actual), (
            "отправление вне окна периода недопустимо"
        )
