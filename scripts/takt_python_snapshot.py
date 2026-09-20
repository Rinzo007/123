#!/usr/bin/env python3
"""Run the canonical synthetic P3 case through passenger_flow and emit JSON."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from passenger_flow import TAKT_PERIODS, run_passenger_flow


@dataclass(frozen=True)
class _Stop:
    id: int
    name: str
    latitude: float
    longitude: float


@dataclass(frozen=True)
class _Direction:
    name: str
    stops: tuple[_Stop, ...]
    segLen: tuple[float, ...] | None = None


@dataclass(frozen=True)
class _HarnessZones:
    ids: np.ndarray
    polygons: tuple[object, ...]
    xy: np.ndarray
    bounds: tuple[float, float, float, float]

    def __len__(self) -> int:
        return int(self.ids.shape[0])


@dataclass(frozen=True)
class _Route:
    ok: bool
    route_id: int
    name: str
    route_type: str
    directions: tuple[_Direction, ...]
    bothWays: bool = False
    headways: tuple[float, ...] = (10, 10, 10, 10, 10)


def build_case() -> tuple[list[_Route], np.ndarray, Zones, np.ndarray]:
    stops = (
        _Stop(1, "A", 52.3700, 0.0000),
        _Stop(2, "B", 52.3700, 0.0050),
    )
    direction = _Direction("A-B", stops, segLen=(340.0,))
    route = _Route(ok=True, route_id=1, name="Bus 1", route_type="bus", directions=(direction,))
    od = np.array([[0.0, 100.0], [100.0, 0.0]], dtype=np.float64)
    points = np.array(
        [[0.0000, 52.3700, 1000.0], [0.0050, 52.3700, 1000.0]],
        dtype=np.float64,
    )
    zones = _HarnessZones(
        ids=np.array([0, 1], dtype=np.int64),
        polygons=(None, None),
        xy=points[:, :2],
        bounds=(-0.001, -0.001, 0.006, 0.001),
    )
    base_time_s = np.full((5, 2), 300.0, dtype=np.float64)
    return [route], od, zones, base_time_s


def main() -> int:
    routes, od, zones, base_time_s = build_case()
    result = run_passenger_flow(
        routes,
        od,
        zones,
        base_time_s=base_time_s,
        car_base_time_s=np.ones((2, 2), dtype=np.float64),
        periods=TAKT_PERIODS,
        headway_min=10.0,
        headway_by_route={1: 10.0},
        msa_max_iterations=2,
        include_reliability=True,
    )
    line = result.line_results[0] if result.line_results else None
    raw_assigned = float(result.raw_assigned_trips)
    raw_car = float(result.raw_car_trips)
    raw_walk = float(result.raw_walk_trips)
    raw_two_wheel = float(result.raw_two_wheel_trips)
    raw_rest = float(result.raw_rest_trips)
    mode_den = max(raw_assigned + raw_car + raw_walk + raw_two_wheel + raw_rest, 1e-12)
    js_round = lambda value: math.floor(float(value) + 0.5)
    snapshot = {
        "reference": {
            "engine": "passenger_flow Python",
            "case": "synthetic-2-stop-bus",
        },
        "raw": {
            "totalTrips": float(result.total_trips),
            "assignedTrips": raw_assigned,
            "carTrips": raw_car,
            "walkTrips": raw_walk,
            "twoWheelTrips": raw_two_wheel,
            "restTrips": raw_rest,
            "periods": [
                {
                    "key": p.key,
                    "totalTrips": float(p.total_trips),
                    "assignedTrips": float(p.assigned_trips),
                    "carTrips": float(p.car_trips),
                    "walkTrips": float(p.walk_trips),
                    "twoWheelTrips": float(p.two_wheel_trips),
                    "restTrips": float(p.rest_trips),
                }
                for p in result.period_flows
            ],
        },
        "differential": {
            "ridersPerDay": js_round(raw_assigned),
            "capitalCostM": float(result.capital_cost_eur / 1_000_000.0),
            "revenueDay": js_round(result.raw_revenue_day),
            "opexDay": js_round(result.raw_opex_day),
            "transitS": float(result.raw_transit_s),
            "transitSByPeriod": [float(v) for v in result.raw_transit_s_by_period],
            "modeSplit": {
                "transit": raw_assigned / mode_den,
                "car": raw_car / mode_den,
                "walk": (raw_walk + raw_two_wheel) / mode_den,
                "rest": raw_rest / mode_den,
            },
            "line": {
                "route_id": int(line.route_id) if line else None,
                "fleet": float(line.fleet) if line else None,
                "revenueDay": js_round(line.revenue_day) if line else None,
                "opexDay": js_round(line.opex_day) if line else None,
                "cycleKm": float(line.cycle_km) if line else None,
            },
        },
    }
    output = (
        Path(__file__).resolve().parents[1]
        / "tests"
        / "fixtures"
        / "takt_python_snapshot.generated.json"
    )
    if len(__import__("sys").argv) > 1:
        output = Path(__import__("sys").argv[1]).resolve()
    output.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(snapshot, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
