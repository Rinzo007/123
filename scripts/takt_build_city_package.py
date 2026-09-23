#!/usr/bin/env python3
"""Build a portable Takt city package manifest from upstream JSON products."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def blob_sha(path: Path) -> str:
    data = path.read_bytes()
    return hashlib.sha1(f"blob {len(data)}\0".encode("ascii") + data).hexdigest()


def rel(path: Path, base: Path) -> str:
    return path.resolve().relative_to(base.resolve()).as_posix()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True, help="City package/case name.")
    parser.add_argument("--version", default="custom")
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--demand", required=True, type=Path, help="OSM/WorldPop/OD-derived demand JSON.")
    parser.add_argument("--baseline", required=True, type=Path, help="OSM/GTFS-derived route network JSON.")
    parser.add_argument("--purposes", required=True, type=Path, help="Purpose OD JSON.")
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)

    files = {
        "model": args.model.expanduser().resolve(),
        "demand": args.demand.expanduser().resolve(),
        "baseline": args.baseline.expanduser().resolve(),
        "purposes": args.purposes.expanduser().resolve(),
        "bundle": args.bundle.expanduser().resolve(),
    }
    for label, path in files.items():
        if not path.is_file():
            raise SystemExit(f"missing {label}: {path}")

    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    base = output.parent
    manifest = {
        "schema": 1,
        "bundle": {
            "source": rel(files["bundle"], base) if files["bundle"].is_relative_to(base) else str(files["bundle"]),
            "git_blob_sha": blob_sha(files["bundle"]),
        },
        "city_cases": [{
            "name": args.name,
            "version": args.version,
            "model": rel(files["model"], base) if files["model"].is_relative_to(base) else str(files["model"]),
            "demand": rel(files["demand"], base) if files["demand"].is_relative_to(base) else str(files["demand"]),
            "baseline": rel(files["baseline"], base) if files["baseline"].is_relative_to(base) else str(files["baseline"]),
            "purposes": rel(files["purposes"], base) if files["purposes"].is_relative_to(base) else str(files["purposes"]),
            "git_blob_sha": {
                key: blob_sha(files[key]) for key in ("model", "demand", "baseline", "purposes")
            },
            "golden_scenario": {
                "max_od_pairs": 3000,
                "max_purpose_od_pairs": 1500,
                "max_lines": 60,
                "selection": "top trips descending; ties by original row index; lines ranked by coarse endpoint-cell coverage then id",
            },
        }],
    }
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
