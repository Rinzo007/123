"""Паритет эталонной модели (периоды и цели) между TS и model.json.

TS держит литералы, чтобы demand-пайплайн не тянул модель с сервера. Тест
собирает настоящий reference-model.ts локальным tsc и сверяет значения с
model.json: правка модели без правки фронтенда должна ронять suite, а не
расходиться молча. Без node/typescript тест пропускается.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend" / "src"
TSC_JS = SRC.parent / "node_modules" / "typescript" / "lib" / "tsc.js"
MODEL = ROOT / "src" / "transit_planner" / "model.json"

PROBE = """
import { REFERENCE_PERIODS, REFERENCE_PURPOSES } from "./reference-model.js";

process.stdout.write(JSON.stringify({
  periods: REFERENCE_PERIODS,
  purposes: REFERENCE_PURPOSES,
}));
"""


def _ts_model(tmp_path: Path) -> dict | None:
    node = shutil.which("node")
    if node is None or not TSC_JS.exists():
        return None
    result = subprocess.run(
        [
            node,
            str(TSC_JS),
            str(SRC / "reference-model.ts"),
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
        cwd=str(SRC.parent),    )
    if result.returncode != 0:
        pytest.fail(f"tsc не собрал стенд:\n{result.stdout}\n{result.stderr}")
    (tmp_path / "probe.mjs").write_text(PROBE, encoding="utf-8")
    run = subprocess.run(
        [node, str(tmp_path / "probe.mjs")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=tmp_path,
    )
    if run.returncode != 0:
        pytest.fail(f"стенд не отработал:\n{run.stdout}\n{run.stderr}")
    return json.loads(run.stdout)


def _model() -> dict:
    return json.loads(MODEL.read_text(encoding="utf-8"))


def test_periods_match_model_json(tmp_path: Path) -> None:
    ts = _ts_model(tmp_path)
    if ts is None:
        pytest.skip("tsc недоступен")
    expected = [
        [row[0], row[1], row[2], row[3], row[4]] for row in _model()["periods"]
    ]
    actual = [
        [row["key"], row["startMinute"], row["endMinute"], row["outboundShare"], row["returnShare"]]
        for row in ts["periods"]
    ]
    assert actual == expected, "периоды в reference-model.ts разошлись с model.json"


def test_purposes_match_model_json(tmp_path: Path) -> None:
    ts = _ts_model(tmp_path)
    if ts is None:
        pytest.skip("tsc недоступен")
    expected = [list(row) for row in _model()["purposes"]]
    actual = [
        [
            row["key"],
            row["label"],
            row["tripsPerResource"],
            row["attractionDistanceM"],
            row["maxDestinations"],
            list(row["outboundShares"]),
            list(row["returnShares"]),
        ]
        for row in ts["purposes"]
    ]
    assert actual == expected, "цели в reference-model.ts разошлись с model.json"


def test_gravity_parameters_match_python_defaults() -> None:
    """Числа гравитации не должны разъезжаться между TS и Python.

    Раньше значения дублировались: экспортированные `CITY_DEMAND_*` в
    reference-model.ts плюс дефолты функций в demand-model.ts, и паритет
    держался на сверке константы с конфигом Python. Теперь определение одно
    на язык - дефолты аргументов функции, - поэтому сверяются сами литералы
    в исходнике TS с дефолтами сигнатуры Python.
    """
    import re

    sys.path.insert(0, str(ROOT / "src"))
    from inspect import signature

    from transit_planner.reference_demand import build_temporal_demand

    source = (ROOT / "frontend" / "src" / "demand-model.ts").read_text(encoding="utf-8")
    params = signature(build_temporal_demand).parameters

    def ts_default(name: str, fallback: str) -> str:
        match = re.search(rf"{name} = ([0-9.]+)", source)
        assert match is not None, f"{name} не найден в demand-model.ts"
        return match.group(1)

    assert float(ts_default("tripRate", "0.12")) == params["trip_rate"].default
    assert float(ts_default("decay", "0.08")) == params["decay"].default
    assert float(ts_default("speedKph", "30")) == params["speed_kph"].default
