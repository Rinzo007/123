#!/usr/bin/env python3
"""Validate a Takt city package manifest and its JSON inputs.

The validator is intentionally stdlib-only so it can be used before the
JavaScript engine is started. A package may live outside the repository;
relative paths are resolved from the manifest directory.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
from typing import Any


class PackageError(ValueError):
    pass


def load(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise PackageError(f"{path}: invalid JSON: {exc}") from exc


def blob_sha(path: Path) -> str:
    data = path.read_bytes()
    return hashlib.sha1(f"blob {len(data)}\0".encode("ascii") + data).hexdigest()


def resolve(base: Path, value: str, label: str, fallback_root: Path | None = None) -> Path:
    path = Path(value)
    if path.is_absolute():
        path = path.resolve()
    else:
        primary = (base / path).resolve()
        fallback = (fallback_root / path).resolve() if fallback_root else primary
        path = primary if primary.is_file() else fallback
    if not path.is_file():
        raise PackageError(f"{label}: file not found: {path}")
    return path


def finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PackageError(f"{label}: expected number")
    if not (float("-inf") < float(value) < float("inf")):
        raise PackageError(f"{label}: expected finite number")
    return float(value)


def validate_demand(data: Any) -> None:
    if not isinstance(data, dict):
        raise PackageError("demand: root must be an object")
    pts = data.get("pts")
    od = data.get("od")
    if not isinstance(pts, list) or not pts:
        raise PackageError("demand.pts: expected a non-empty array")
    for i, point in enumerate(pts):
        if not isinstance(point, list) or len(point) < 2:
            raise PackageError(f"demand.pts[{i}]: expected [lon, lat]")
        lon = finite_number(point[0], f"demand.pts[{i}][0]")
        lat = finite_number(point[1], f"demand.pts[{i}][1]")
        if not -180 <= lon <= 180 or not -90 <= lat <= 90:
            raise PackageError(f"demand.pts[{i}]: coordinates out of range")
    if not isinstance(od, list):
        raise PackageError("demand.od: expected an array")
    zones = len(pts)
    for i, row in enumerate(od):
        if not isinstance(row, list) or len(row) < 3:
            raise PackageError(f"demand.od[{i}]: expected [origin, destination, trips, ...]")
        oi = int(finite_number(row[0], f"demand.od[{i}][0]"))
        di = int(finite_number(row[1], f"demand.od[{i}][1]"))
        trips = finite_number(row[2], f"demand.od[{i}][2]")
        if not 0 <= oi < zones or not 0 <= di < zones:
            raise PackageError(f"demand.od[{i}]: zone index out of range")
        if trips < 0:
            raise PackageError(f"demand.od[{i}]: trips must be non-negative")


def validate_baseline(data: Any) -> None:
    if not isinstance(data, dict) or not isinstance(data.get("lines"), list):
        raise PackageError("baseline.lines: expected an array")
    for li, line in enumerate(data["lines"]):
        if not isinstance(line, dict):
            raise PackageError(f"baseline.lines[{li}]: expected an object")
        stops = line.get("stops")
        if not isinstance(stops, list) or len(stops) < 2:
            raise PackageError(f"baseline.lines[{li}].stops: expected at least two points")
        for si, point in enumerate(stops):
            if not isinstance(point, list) or len(point) < 2:
                raise PackageError(f"baseline.lines[{li}].stops[{si}]: expected [lon, lat]")
            lon = finite_number(point[0], f"baseline.lines[{li}].stops[{si}][0]")
            lat = finite_number(point[1], f"baseline.lines[{li}].stops[{si}][1]")
            if not -180 <= lon <= 180 or not -90 <= lat <= 90:
                raise PackageError(
                    f"baseline.lines[{li}].stops[{si}]: coordinates out of range"
                )
        headways = line.get("headways", [10, 10, 10, 10, 10])
        if not isinstance(headways, list) or len(headways) != 5:
            raise PackageError(f"baseline.lines[{li}].headways: expected five periods")
        for hi, headway in enumerate(headways):
            if finite_number(headway, f"baseline.lines[{li}].headways[{hi}]") <= 0:
                raise PackageError(f"baseline.lines[{li}].headways[{hi}]: must be > 0")


def validate_purposes(data: Any) -> None:
    if not isinstance(data, dict) or not isinstance(data.get("layers"), list):
        raise PackageError("purposes.layers: expected an array")
    for li, layer in enumerate(data["layers"]):
        if not isinstance(layer, dict):
            raise PackageError(f"purposes.layers[{li}]: expected an object")
        if not isinstance(layer.get("od"), str):
            raise PackageError(f"purposes.layers[{li}].od: expected base64 string")
        try:
            decoded = base64.b64decode(layer["od"], validate=True)
        except (ValueError, TypeError) as exc:
            raise PackageError(f"purposes.layers[{li}].od: invalid base64") from exc
        n = layer.get("n")
        if not isinstance(n, int) or n < 0 or len(decoded) < n * 4 * 4:
            raise PackageError(f"purposes.layers[{li}]: invalid n/od payload")
        for key in ("out", "ret"):
            values = layer.get(key)
            if not isinstance(values, list):
                raise PackageError(f"purposes.layers[{li}].{key}: expected an array")


def validate_manifest(path: Path, fallback_root: Path | None = None) -> dict[str, Any]:
    data = load(path)
    if not isinstance(data, dict):
        raise PackageError("manifest: root must be an object")
    cases = data.get("city_cases")
    bundle = data.get("bundle")
    if not isinstance(cases, list) or not cases:
        raise PackageError("manifest.city_cases: expected a non-empty array")
    if not isinstance(bundle, dict) or not isinstance(bundle.get("source"), str):
        raise PackageError("manifest.bundle.source: required")
    base = path.parent
    bundle_path = resolve(base, bundle["source"], "bundle", fallback_root)
    expected_bundle = bundle.get("git_blob_sha")
    if expected_bundle:
        if blob_sha(bundle_path) != expected_bundle:
            raise PackageError("bundle: git_blob_sha mismatch")
    names: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            raise PackageError("city_cases entries must be objects")
        name = case.get("name")
        if not isinstance(name, str) or not name:
            raise PackageError("city case: missing name")
        if name in names:
            raise PackageError(f"duplicate city case: {name}")
        names.add(name)
        for field in ("model", "demand", "baseline", "purposes"):
            resolve(base, case.get(field, ""), f"{name}.{field}", fallback_root)
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args(argv)
    path = args.manifest.expanduser().resolve()
    fallback_root = Path.cwd().resolve()
    try:
        manifest = validate_manifest(path, fallback_root)
    except (OSError, PackageError) as exc:
        print(f"takt-package: FAIL: {exc}")
        return 1

    base = path.parent
    for case in manifest["city_cases"]:
        name = case["name"]
        demand = load(resolve(base, case["demand"], f"{name}.demand", fallback_root))
        baseline = load(resolve(base, case["baseline"], f"{name}.baseline", fallback_root))
        purposes = load(resolve(base, case["purposes"], f"{name}.purposes", fallback_root))
        load(resolve(base, case["model"], f"{name}.model", fallback_root))
        validate_demand(demand)
        validate_baseline(baseline)
        validate_purposes(purposes)
        print(f"{name}: demand/baseline/purposes/model OK")
    print(f"takt-package: OK ({len(manifest['city_cases'])} city case(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
