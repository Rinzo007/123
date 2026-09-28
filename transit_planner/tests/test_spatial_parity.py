"""Паритет пространственного индекса TS и spatial.py.

Python здесь эталон. Проверка собирает настоящий spatial.ts локальным tsc и
сравнивает ответы по точному равенству: обе реализации обязаны считать
одинаково, включая последний бит расстояния, поэтому в TS запрещён Math.hypot
(он аккуратнее sqrt и отличается от Python).
"""
from __future__ import annotations

import json
import random
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from transit_planner.spatial import GridPointIndex, IndexedPoint  # noqa: E402

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
SRC = FRONTEND / "src"
TS_MODULE = SRC / "spatial.ts"
TSC_JS = FRONTEND / "node_modules" / "typescript" / "lib" / "tsc.js"
CELL_SIZE = 100.0

PROBE = """
import { buildGridIndex, gridNearest, gridWithinRadius } from "./spatial.js";

const spec = JSON.parse(process.argv[2]);
const index = buildGridIndex(Float64Array.from(spec.points), Float64Array.from(spec.others), spec.cellSize);
const nearest = spec.queries.map(([x, y, radius]) => {
  const found = gridNearest(index, x, y, radius);
  return found === null ? null : [found.index, found.distance];
});
const within = spec.queries.map(([x, y, radius]) => gridWithinRadius(index, x, y, radius));
process.stdout.write(JSON.stringify({ nearest, within }));
"""


def _fixture() -> tuple[list[tuple[float, float]], list[tuple[float, float, float]]]:
    rng = random.Random(20240517)
    points = [(rng.uniform(-5000, 5000), rng.uniform(-5000, 5000)) for _ in range(400)]
    # Точки на границах ячеек и совпадающие — проверка деления на ячейки.
    points += [(0.0, 0.0), (100.0, 100.0), (-100.0, -100.0), (0.0, 100.0)]
    queries = [
        (0.0, 0.0, 50.0),
        (123.456, -321.654, 250.0),
        (-4999.0, 4999.0, 1000.0),
        (5001.0, 5001.0, 100.0),
        (0.0, 0.0, 0.0),
        (7000.0, -7000.0, 50.0),
        (12.5, 12.5, 10000.0),
    ]
    return points, queries


def _ts_answers(tmp_path: Path) -> dict:
    node = shutil.which("node")
    if node is None or not TSC_JS.exists():
        return {}
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
        cwd=FRONTEND,
    )
    if result.returncode != 0:
        pytest.fail(f"tsc не собрал стенд:\n{result.stdout}\n{result.stderr}")
    (tmp_path / "probe.mjs").write_text(PROBE, encoding="utf-8")
    points, queries = _fixture()
    payload = json.dumps(
        {
            "points": [x for x, _ in points],
            "others": [y for _, y in points],
            "queries": queries,
            "cellSize": CELL_SIZE,
        }
    )
    run = subprocess.run(
        [node, str(tmp_path / "probe.mjs"), payload],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    if run.returncode != 0:
        pytest.fail(f"стенд не отработал:\n{run.stdout}\n{run.stderr}")
    return json.loads(run.stdout)


def _python_answers() -> dict:
    points, queries = _fixture()
    index = GridPointIndex(cell_size=CELL_SIZE)
    for position, (x, y) in enumerate(points):
        index.insert(IndexedPoint(position, x, y))
    nearest = []
    within = []
    for x, y, radius in queries:
        found = index.nearest(x, y, max_radius=radius)
        nearest.append(None if found is None else [found.id, distance(points, found.id, x, y)])
        within.append(sorted(index_ids(index, points, x, y, radius)))
    return {"nearest": nearest, "within": within}


def distance(points: list[tuple[float, float]], position: int, x: float, y: float) -> float:
    px, py = points[position]
    return ((px - x) ** 2 + (py - y) ** 2) ** 0.5


def index_ids(
    index: GridPointIndex,
    points: list[tuple[float, float]],
    x: float,
    y: float,
    radius: float,
) -> list[int]:
    return [
        position
        for position, (px, py) in enumerate(points)
        if ((px - x) ** 2 + (py - y) ** 2) ** 0.5 <= radius
    ]


@pytest.mark.skipif(
    not TSC_JS.exists() or shutil.which("node") is None,
    reason="node или typescript не установлены: TS-паритет не проверяется",
)
def test_spatial_index_matches_python(tmp_path: Path) -> None:
    ts = _ts_answers(tmp_path)
    if not ts:
        pytest.skip("tsc недоступен")
    expected = _python_answers()
    assert ts["nearest"] == expected["nearest"], (
        "ближайшая точка или расстояние разошлись с spatial.py"
    )
    assert ts["within"] == expected["within"], (
        "состав точек в радиусе разошёлся с spatial.py"
    )


def test_spatial_index_is_exact_against_brute_force() -> None:
    """Grid обязан совпадать с полным перебором, иначе он не exact."""
    points, queries = _fixture()
    index = GridPointIndex(cell_size=CELL_SIZE)
    for position, (x, y) in enumerate(points):
        index.insert(IndexedPoint(position, x, y))
    for x, y, radius in queries:
        found = index.nearest(x, y, max_radius=radius)
        inside = [
            position
            for position, (px, py) in enumerate(points)
            if ((px - x) ** 2 + (py - y) ** 2) ** 0.5 <= radius
        ]
        if not inside:
            assert found is None, "точка вне радиуса не должна находиться"
            continue
        best = min(inside, key=lambda position: distance(points, position, x, y))
        assert found is not None, "точка в радиусе обязана находиться"
        assert distance(points, found.id, x, y) == distance(points, best, x, y)
