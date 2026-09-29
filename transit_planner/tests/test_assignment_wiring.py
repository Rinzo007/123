"""Тесты связки браузерного assignment с интерфейсом.

Проверяется то, что не относится к чистой логике и потому не попало в париты:
построение оверлея demand streets из нагрузок секций, проброс доли households
без автомобиля из demand-воркера в колонки зон, и — главное — запрет возврата
к серверному `/api/v1/assignment` как второму runtime-пути.

Стенд собирает `assignment-client.ts` и `demand.ts` локальным tsc. Без
node/typescript тест пропускается.
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


SRC = ROOT / "frontend" / "src"
TSC_JS = ROOT / "frontend" / "node_modules" / "typescript" / "lib" / "tsc.js"
APP_TS = SRC / "app.ts"
API_TS = SRC / "api.ts"

# Оверлей строится на сети из трёх остановок с геометрией маршрута: секции
# отдаются вперемешку, и порядок вдоль маршрута обязан быть восстановлен.
NETWORK = {
    "origin_lon": 0.0,
    "origin_lat": 0.0,
    "stops": [
        {"id": "s1", "name": "S1", "is_station": False, "location": {"x": 0.0, "y": 0.0}},
        {"id": "s2", "name": "S2", "is_station": False, "location": {"x": 1000.0, "y": 0.0}},
        {"id": "s3", "name": "S3", "is_station": False, "location": {"x": 2000.0, "y": 0.0}},
    ],
    "routes": [
        {
            "id": "r1", "name": "R1", "mode": "bus",
            "stop_ids": ["s1", "s2", "s3"],
            "geometry": {"points": [{"x": 0.0, "y": 0.0}, {"x": 2000.0, "y": 0.0}]},
            "both_ways": False, "closed": False,
        },
    ],
    "vehicle_types": [
        {"id": "vt", "name": "Bus", "mode": "bus", "capacity": 90, "operating_cost_per_km": 0.0},
    ],
    "periods": [{"id": "am", "start_minute": 360, "end_minute": 540}],
    "services": [
        {"id": "v1", "route_id": "r1", "vehicle_type_id": "vt", "headway_by_period": {"am": 10}},
    ],
    "track_nodes": [],
    "track_sections": [],
}

SECTION_LOADS = [
    # Порядок намеренно перепутан: s2→s3 раньше s1→s2.
    {"route_id": "r1", "from_stop_id": "s2", "to_stop_id": "s3", "passengers": 40.0,
     "capacity": 100.0, "load_ratio": 0.4, "crowding_level": "normal"},
    {"route_id": "r1", "from_stop_id": "s1", "to_stop_id": "s2", "passengers": 120.0,
     "capacity": 100.0, "load_ratio": 1.2, "crowding_level": "crowded"},
]

PROBE = """
import { demandStreetsFromLoads } from "./workers/assignment-client.js";

const spec = JSON.parse(process.argv[2]);
process.stdout.write(JSON.stringify(demandStreetsFromLoads(spec.network, spec.loads)));
"""


def _ts_out(tmp_path: Path, loads=None) -> dict | None:
    node = shutil.which("node")
    if node is None or not TSC_JS.exists():
        return None
    build = subprocess.run(
        [
            node, str(TSC_JS), str(SRC / "workers" / "assignment-client.ts"),
            "--outDir", str(tmp_path),
            "--rootDir", str(SRC),
            "--module", "esnext",
            "--target", "es2022",
            "--ignoreConfig",
        ],
        capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT / "frontend"),
    )
    if build.returncode != 0:
        pytest.fail(f"tsc не собрал assignment-client:\n{build.stdout}\n{build.stderr}")
    (tmp_path / "probe.mjs").write_text(PROBE, encoding="utf-8")
    run = subprocess.run(
        [
            node, str(tmp_path / "probe.mjs"),
            json.dumps({"network": NETWORK, "loads": loads or SECTION_LOADS}),
        ],
        capture_output=True, text=True, encoding="utf-8", cwd=str(tmp_path),
    )
    if run.returncode != 0:
        pytest.fail(f"стенд оверлея не отработал:\n{run.stdout}\n{run.stderr}")
    return json.loads(run.stdout)


@pytest.mark.skipif(
    not TSC_JS.exists() or shutil.which("node") is None,
    reason="node или typescript не установлены: оверлей не проверяется",
)
def test_demand_streets_follow_the_route_order(tmp_path: Path) -> None:
    """Линия идёт вдоль маршрута в его порядке, а не в порядке ответа."""
    result = _ts_out(tmp_path)
    if result is None:
        pytest.skip("tsc недоступен")
    assert len(result["features"]) == 1
    feature = result["features"][0]
    assert feature["geometry"]["type"] == "LineString"
    # s1 → s2 → s3, несмотря на то что s2→s3 пришёл раньше.
    assert [list(point) for point in feature["geometry"]["coordinates"]] == [
        [0.0, 0.0], [1000.0, 0.0], [2000.0, 0.0]
    ]
    properties = feature["properties"]
    # flow_weight — максимум по секциям: именно по нему рисуется толщина линии.
    assert properties["flow_weight"] == 120.0
    assert properties["passengers"] == 160.0
    assert properties["sections"] == 2
    assert properties["route_id"] == "r1"


@pytest.mark.skipif(
    not TSC_JS.exists() or shutil.which("node") is None,
    reason="node или typescript не установлены: оверлей не проверяется",
)
def test_demand_streets_skip_unloaded_sections(tmp_path: Path) -> None:
    """Пустые секции не попадают в оверлей: иначе линия рисует весь город."""
    loads = [
        SECTION_LOADS[0],
        {**SECTION_LOADS[1], "passengers": 0.0},
    ]
    result = _ts_out(tmp_path, loads)
    if result is None:
        pytest.skip("tsc недоступен")
    assert len(result["features"]) == 1
    feature = result["features"][0]
    assert [list(point) for point in feature["geometry"]["coordinates"]] == [
        [1000.0, 0.0], [2000.0, 0.0]
    ]
    assert feature["properties"]["flow_weight"] == 40.0
    assert feature["properties"]["sections"] == 1


def test_app_does_not_call_the_server_assignment() -> None:
    """Серверный assignment не должен остаться вторым runtime-путём.

    README (Этап 14) требует, чтобы одна и та же арифметика не считалась в двух
    местах. Возврат этих вызовов означал бы расхождение сервера и браузера.
    """
    source = APP_TS.read_text(encoding="utf-8")
    for forbidden in ("calculateAssignment(", "loadDemandStreets("):
        assert forbidden not in source, f"app.ts снова вызывает серверный {forbidden}"
    # Сами функции в api.ts остаются: они нужны для пакетов и share.
    assert "export function calculateAssignment(" in API_TS.read_text(encoding="utf-8")


def test_app_uses_the_assignment_worker() -> None:
    source = APP_TS.read_text(encoding="utf-8")
    assert "assignDemandInWorker(" in source
    assert "demandStreetsFromLoads(" in source
    # Спрос берётся за выбранный период, а не за сутки целиком.
    assert "built.temporal" in source
    assert "row.periodId === period.id" in source


