#!/usr/bin/env python3
"""Reproducible P4 benchmarks for passenger_flow hot paths and full OD assignment."""

from __future__ import annotations

import argparse
import cProfile
import pstats
import time
from types import SimpleNamespace

import numpy as np

from passenger_flow.algorithm.assign import _assign_od
from passenger_flow.base.models import ModeChoiceConfig
from passenger_flow.network.routes import (
    _build_transfer_edge_index,
    _route_ride_time_min,
)


def synthetic_sequences(lines: int, stops_per_line: int) -> list[dict]:
    sequences = []
    for seq_idx in range(lines):
        stops = []
        for pos in range(stops_per_line):
            stops.append({
                "id": seq_idx * stops_per_line + pos,
                "name": str(pos),
                "lat": 52.0 + pos * 0.0005,
                "lon": 4.0 + seq_idx * 0.0002 + pos * 0.0005,
                "position": pos,
            })
        sequences.append({
            "_seq_idx": seq_idx,
            "route_id": seq_idx + 1,
            "route_name": f"synthetic-{seq_idx + 1}",
            "route_type": "tram",
            "route_type_key": "tram",
            "access_m": 600.0,
            "di": 0,
            "direction_name": "A",
            "stops": stops,
            "cum_t_s": [float(i * 90) for i in range(stops_per_line)],
            "cycle_run_s": float(max(0, stops_per_line - 1) * 90),
            "dwell_s": 25.0,
            "speed_kmh": 19.0,
            "closed": False,
            "both_ways": False,
            "open": [True] * stops_per_line,
            "open_pre": list(range(stops_per_line + 1)),
        })
    return sequences


def synthetic_assignment_case(
    lines: int,
    stops_per_line: int,
    zones: int,
    od_pairs: int,
) -> tuple[
    np.ndarray,
    np.ndarray,
    np.ndarray,
    dict[int, list[tuple[int, int, int, float]]],
    list[dict],
    SimpleNamespace,
    dict[tuple[int, int], tuple[tuple[int, dict, dict], ...]],
]:
    sequences = synthetic_sequences(lines, stops_per_line)
    xy = []
    zone_nearest = {}
    for zone in range(zones):
        seq_idx = zone % lines
        pos = (zone // lines) % stops_per_line
        stop = sequences[seq_idx]["stops"][pos]
        xy.append([stop["lon"], stop["lat"]])
        zone_nearest[zone] = [(seq_idx, pos, pos, 0.0)]

    rows = []
    cols = []
    for i in range(od_pairs):
        zi = i % zones
        zj = (i * 17 + 11) % zones
        if zi == zj:
            zj = (zj + 1) % zones
        rows.append(zi)
        cols.append(zj)

    od_vals = np.full(len(rows), 40.0, dtype=np.float64)
    return (
        np.asarray(rows, dtype=np.int64),
        np.asarray(cols, dtype=np.int64),
        od_vals,
        zone_nearest,
        sequences,
        SimpleNamespace(xy=np.asarray(xy, dtype=np.float64)),
        _build_transfer_edge_index(sequences, 800.0),
    )


def run_full_assignment(
    od_rows: np.ndarray,
    od_cols: np.ndarray,
    od_vals: np.ndarray,
    zone_nearest: dict[int, list[tuple[int, int, int, float]]],
    sequences: list[dict],
    zones: SimpleNamespace,
    transfer_index: dict[tuple[int, int], tuple[tuple[int, dict, dict], ...]],
) -> dict:
    seg_load = {
        (seq_idx, seg_idx): 0.75
        for seq_idx in range(len(sequences))
        for seg_idx in range(max(0, len(sequences[seq_idx]["stops"]) - 1))
    }
    crowd_state = {
        "seg_forward": seg_load,
        "seg_reverse": seg_load,
        "stop_extra": {},
        "unreliability": {
            (seq_idx, 0): 1.03 for seq_idx in range(len(sequences))
        },
    }
    return _assign_od(
        od_rows,
        od_cols,
        od_vals,
        zone_nearest,
        sequences,
        stop_time_min=0.0,
        wait_time_min=0.0,
        walk_to_stop_min=0.0,
        transfer_penalty_min=2.0,
        transfer_wait_min=4.0,
        transfer_radius_m=800.0,
        max_transfers=3,
        logit_temp=1.0,
        out_factor=1.0,
        ret_factor=0.0,
        mode=ModeChoiceConfig(),
        zones=zones,
        transfer_penalty_calc="takt",
        seq_headway_min={i: 10.0 for i in range(len(sequences))},
        seq_jitter_s={i: 60.0 for i in range(len(sequences))},
        wait_calc="takt",
        crowd_state=crowd_state,
        transfer_index=transfer_index,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lines", type=int, default=40)
    parser.add_argument("--stops", type=int, default=30)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--assignment-lines", type=int, default=12)
    parser.add_argument("--assignment-stops", type=int, default=15)
    parser.add_argument("--assignment-zones", type=int, default=48)
    parser.add_argument("--od-pairs", type=int, default=240)
    parser.add_argument("--profile", action="store_true")
    args = parser.parse_args()

    sequences = synthetic_sequences(args.lines, args.stops)
    build_times = []
    ride_times = []
    for _ in range(max(1, args.repeats)):
        start = time.perf_counter()
        index = _build_transfer_edge_index(sequences, 800.0)
        build_times.append(time.perf_counter() - start)
        start = time.perf_counter()
        sink = 0.0
        for seq in sequences:
            for pos in range(args.stops - 1):
                sink += _route_ride_time_min(seq, pos, args.stops - 1)
        ride_times.append(time.perf_counter() - start)

    case = synthetic_assignment_case(
        args.assignment_lines,
        args.assignment_stops,
        args.assignment_zones,
        args.od_pairs,
    )
    assign_times = []
    assignment_result = None
    repeats = max(1, args.repeats)
    for repeat_no in range(repeats):
        start = time.perf_counter()
        if args.profile and repeat_no == 0:
            profiler = cProfile.Profile()
            assignment_result = profiler.runcall(run_full_assignment, *case)
            profiler.create_stats()
            print("\nfull assignment profile:")
            pstats.Stats(profiler).sort_stats("cumulative").print_stats(12)
        else:
            assignment_result = run_full_assignment(*case)
        assign_times.append(time.perf_counter() - start)

    median = lambda values: sorted(values)[len(values) // 2]
    print(f"transfer_index_entries={len(index)}")
    print(f"ride_sink={sink:.3f}")
    print(f"transfer_index_seconds_min={min(build_times):.6f}")
    print(f"transfer_index_seconds_median={median(build_times):.6f}")
    print(f"ride_seconds_min={min(ride_times):.6f}")
    print(f"ride_seconds_median={median(ride_times):.6f}")
    print(f"full_assignment_od_pairs={args.od_pairs}")
    print(f"full_assignment_routes={len(case[4])}")
    print(f"full_assignment_assigned={assignment_result['assigned_trips']:.6f}")
    print(f"full_assignment_seconds_min={min(assign_times):.6f}")
    print(f"full_assignment_seconds_median={median(assign_times):.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
