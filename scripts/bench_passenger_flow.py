#!/usr/bin/env python3
"""Small reproducible P4 benchmarks for passenger_flow hot paths."""

from __future__ import annotations

import argparse
import time

from passenger_flow.network.routes import _build_transfer_edge_index, _route_ride_time_min


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
            "route_type_key": "tram",
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lines", type=int, default=40)
    parser.add_argument("--stops", type=int, default=30)
    parser.add_argument("--repeats", type=int, default=5)
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

    print(f"transfer_index_entries={len(index)}")
    print(f"ride_sink={sink:.3f}")
    print(f"transfer_index_seconds_min={min(build_times):.6f}")
    print(f"transfer_index_seconds_median={sorted(build_times)[len(build_times)//2]:.6f}")
    print(f"ride_seconds_min={min(ride_times):.6f}")
    print(f"ride_seconds_median={sorted(ride_times)[len(ride_times)//2]:.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())