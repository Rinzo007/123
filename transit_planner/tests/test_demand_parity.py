"""Паритет demand-модели: TS gravityOd/buildDailyDemand ↔ Python.

Python здесь эталон. Проверка собирает настоящий demand-model.ts локальным
tsc и сверяет OD-матрицу по поездкам и базовому времени. Расхождение
допускается только на уровне порядка величин (1e-9), потому что обе стороны
суммируют веса в своём порядке обхода; геометрия и коэффициенты должны
совпадать точно.

Модуль demand-model.ts тянет только ./projection и не имеет DOM-зависимостей,
поэтому emitted JS самодостаточен. Без node/typescript тест пропускается.
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

from transit_planner.city import DemandZone  # noqa: E402
from transit_planner.od import GravityParameters, gravity_od  # noqa: E402

SRC = ROOT / "frontend" / "src"
TSC_JS = ROOT / "frontend" / "node_modules" / "typescript" / "lib" / "tsc.js"

# Зоны с ненулевыми jobs и population, плюс intrazonal-пара и дальняя пара.
ZONES = [
    ("z1", 0.0, 0.0, 1000.0, 100.0),
    ("z2", 800.0, 0.0, 200.0, 900.0),
    ("z3", 0.0, 1500.0, 50.0, 50.0),
]
SPEED_KPH = 30.0
DECAY = 0.08
INTRAZONAL = 0.5
TOLERANCE = 1e-9

PROBE = """
import { gravityOd } from "./demand-model.js";

const spec = JSON.parse(process.argv[2]);
const zones = spec.zones.map(([id, x, y, population, jobs]) => ({
  id, centroidX: x, centroidY: y, population, jobs,
  attractions: {}, }));
const matrix = gravityOd(zones, {
  speedKph: spec.speedKph, decay: spec.decay, intrazonalFactor: spec.intrazonal,
});
process.stdout.write(JSON.stringify(matrix.pairs.map((p) => [
  p.originZoneId, p.destinationZoneId, p.tripsPerDay, p.baseTimeMin,
])));
"""


def _ts_pairs(tmp_path: Path) -> list[tuple[str, str, float, float | None]] | None:
    node = shutil.which("node")
    if node is None or not TSC_JS.exists():
        return None
    result = subprocess.run(
        [
            node,
            str(TSC_JS),
            str(SRC / "demand-model.ts"),
            str(SRC / "projection.ts"),
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
        cwd=str(ROOT / "frontend"),
    )
    if result.returncode != 0:
        pytest.fail(f"tsc не собрал стенд:\n{result.stdout}\n{result.stderr}")
    # tsc оставляет extensionless-импорт, а Node ESM его не разрешает.
    compiled = tmp_path / "demand-model.js"
    compiled.write_text(
        compiled.read_text(encoding="utf-8").replace('from "./projection"', 'from "./projection.js"'),
        encoding="utf-8",
    )
    (tmp_path / "probe.mjs").write_text(PROBE, encoding="utf-8")
    payload = json.dumps(
        {
            "zones": [list(zone) for zone in ZONES],
            
            "speedKph": SPEED_KPH,
            "decay": DECAY,
            "intrazonal": INTRAZONAL,
        }
    )
    run = subprocess.run(
        [node, str(tmp_path / "probe.mjs"), payload],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(tmp_path),
    )
    if run.returncode != 0:
        pytest.fail(f"стенд не отработал:\n{run.stdout}\n{run.stderr}")
    return json.loads(run.stdout)


def _python_pairs() -> list[tuple[str, str, float, float | None]]:
    zones = tuple(
        DemandZone(zid, x, y, population=population, jobs=jobs)
        for zid, x, y, population, jobs in ZONES
    )
    matrix = gravity_od(
        zones,
        parameters=GravityParameters(
            speed_kph=SPEED_KPH,
            decay=DECAY,
            intrazonal_factor=INTRAZONAL,
        ),
    )
    return [
        (pair.origin_zone_id, pair.destination_zone_id, pair.trips_per_day, pair.base_time_min)
        for pair in matrix.pairs
    ]


@pytest.mark.skipif(
    not TSC_JS.exists() or shutil.which("node") is None,
    reason="node или typescript не установлены: TS-паритет не проверяется",
)
def test_gravity_od_matches_python(tmp_path: Path) -> None:
    ts = _ts_pairs(tmp_path)
    if ts is None:
        pytest.skip("tsc недоступен")
    expected = _python_pairs()
    assert ts is not None
    assert len(ts) == len(expected), (
        f"разное число пар: TS {len(ts)} != Python {len(expected)}"
    )
    for actual, reference in zip(ts, expected, strict=True):
        assert actual[0] == reference[0], f"origin: {actual} != {reference}"
        assert actual[1] == reference[1], f"destination: {actual} != {reference}"
        assert abs(actual[2] - reference[2]) <= TOLERANCE, (
            f"trips {actual[0]}->{actual[1]}: TS {actual[2]} != Python {reference[2]}"
        )
        assert actual[3] is not None and reference[3] is not None
        assert abs(actual[3] - reference[3]) <= TOLERANCE, (
            f"base_time {actual[0]}->{actual[1]}: TS {actual[3]} != Python {reference[3]}"
        )


def test_gravity_od_conserves_productions() -> None:
    """R независимо от разбиения: сумма поездок = production каждой зоны.

    Production - это занятость зоны: одна поездка на работника в сутки.
    Множителя `население x 0.12` больше нет.
    """
    pairs = _python_pairs()
    by_origin: dict[str, float] = {}
    for origin, _destination, trips, _base in pairs:
        by_origin[origin] = by_origin.get(origin, 0.0) + trips
    for zone_id, _x, _y, _population, jobs in ZONES:
        assert by_origin[zone_id] == pytest.approx(jobs, abs=1e-9)
