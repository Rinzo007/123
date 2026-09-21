#!/usr/bin/env python3
"""Fail-closed release gate for pinned Takt parity data and snapshots."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


class ReleaseGateError(ValueError):
    """Raised when release parity invariants are violated."""


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseGateError(f"{path}: cannot read JSON: {exc}") from exc


def git_blob_sha(path: Path) -> str:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ReleaseGateError(f"{path}: cannot read bundle: {exc}") from exc
    return hashlib.sha1(f"blob {len(data)}\0".encode("ascii") + data).hexdigest()


def verify_bundle_provenance(reference: dict[str, Any], repo_root: Path) -> str:
    provenance = reference.get("reference")
    if not isinstance(provenance, dict):
        raise ReleaseGateError("reference snapshot is missing reference metadata")

    source = provenance.get("source")
    expected_sha = provenance.get("bundle_git_blob_sha")
    bundle_name = provenance.get("bundle")
    if not isinstance(source, str) or not source:
        raise ReleaseGateError("reference metadata has no bundle source path")
    if not isinstance(expected_sha, str) or len(expected_sha) != 40:
        raise ReleaseGateError("reference metadata has no valid bundle_git_blob_sha")
    if not isinstance(bundle_name, str) or not bundle_name:
        raise ReleaseGateError("reference metadata has no bundle filename")
    if Path(source).name != bundle_name:
        raise ReleaseGateError(
            f"bundle filename mismatch: source={source!r}, bundle={bundle_name!r}"
        )

    bundle_path = repo_root / source
    actual_sha = git_blob_sha(bundle_path)
    if actual_sha != expected_sha:
        raise ReleaseGateError(
            f"pinned Takt bundle changed: expected {expected_sha}, got {actual_sha}"
        )
    return actual_sha


def _require_case_field(case: dict[str, Any], field: str, case_name: str) -> str:
    value = case.get(field)
    if not isinstance(value, str) or not value:
        raise ReleaseGateError(f"{case_name}: missing string field {field!r}")
    return value


def validate_city_manifest(manifest: dict[str, Any], repo_root: Path) -> list[str]:
    if manifest.get("schema") != 1:
        raise ReleaseGateError("city manifest schema must be 1")

    bundle = manifest.get("bundle")
    if not isinstance(bundle, dict):
        raise ReleaseGateError("city manifest is missing bundle metadata")
    bundle_source = bundle.get("source")
    bundle_sha = bundle.get("git_blob_sha")
    if not isinstance(bundle_source, str) or not isinstance(bundle_sha, str):
        raise ReleaseGateError("city manifest bundle metadata is incomplete")

    bundle_path = repo_root / bundle_source
    actual_sha = git_blob_sha(bundle_path)
    if actual_sha != bundle_sha:
        raise ReleaseGateError(
            f"city manifest bundle pin mismatch: expected {bundle_sha}, got {actual_sha}"
        )

    cases = manifest.get("city_cases")
    if not isinstance(cases, list) or not cases:
        raise ReleaseGateError("city manifest must contain at least one city case")

    names: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ReleaseGateError("city_cases entries must be objects")
        name = _require_case_field(case, "name", "<unknown>")
        if name in names:
            raise ReleaseGateError(f"duplicate city case {name!r}")
        names.add(name)

        for field in ("model", "demand", "baseline", "purposes"):
            rel = _require_case_field(case, field, name)
            path = repo_root / rel
            if not path.is_file():
                raise ReleaseGateError(f"{name}: missing source file {rel}")

        expected_shas = case.get("git_blob_sha")
        if not isinstance(expected_shas, dict):
            raise ReleaseGateError(f"{name}: missing git_blob_sha map")
        for field in ("model", "demand", "baseline", "purposes"):
            expected = expected_shas.get(field)
            if not isinstance(expected, str) or len(expected) != 40:
                raise ReleaseGateError(f"{name}: invalid SHA for {field}")
            actual = git_blob_sha(repo_root / case[field])
            if actual != expected:
                raise ReleaseGateError(
                    f"{name}: {field} changed: expected {expected}, got {actual}"
                )

    return sorted(names)


def verify_city_snapshots(
    manifest: dict[str, Any],
    repo_root: Path,
    *,
    require: bool,
    check_schema: bool = False,
) -> list[str]:
    missing: list[str] = []
    for case in manifest["city_cases"]:
        name = case["name"]
        js_rel = case.get("js_snapshot")
        py_rel = case.get("python_snapshot")
        if not isinstance(js_rel, str) or not isinstance(py_rel, str):
            missing.append(name)
            continue
        js_path = repo_root / js_rel
        py_path = repo_root / py_rel
        if not js_path.is_file() or not py_path.is_file():
            missing.append(name)
            continue
        # Import lazily so the gate stays dependency-free.
        from scripts.takt_differential import compare_snapshots

        js_snapshot = load_json(js_path)
        py_snapshot = load_json(py_path)
        js_parity = dict(js_snapshot.get("parity", js_snapshot.get("differential", {})) or {})
        py_parity = dict(py_snapshot.get("parity", py_snapshot.get("differential", {})) or {})
        # Satisfaction causes remain in the full result for schema/audit coverage;
        # the release comparison uses the stable score/trip scalar only.
        for parity in (js_parity, py_parity):
            sat = parity.pop("satisfaction", None)
            if isinstance(sat, dict):
                parity["satisfactionScore"] = sat.get("score")
                parity["satisfactionTotalTrips"] = sat.get("totalTrips")
        differences = compare_snapshots(js_parity, py_parity)
        if differences:
            first = differences[0]
            raise ReleaseGateError(
                f"{name}: JS/Python parity mismatch at {first.path}: "
                f"{first.detail}"
            )
        if check_schema:
            required = {
                "parity": (
                    "ridersPerDay", "capitalCostM", "revenueDay", "opexDay",
                    "modeSplit", "transferTrips", "coveredCommuters", "totalCommuters",
                ),
                "result": (
                    "satisfaction", "interchanges", "trackCapacity",
                    "coveredPoint", "servedByPoint", "missedByPoint",
                    "noRouteByPoint", "journeyOrigins", "equilibrium",
                    "lines", "periods", "stops",
                ),
            }
            for section, keys in required.items():
                js_part = js_snapshot.get(section)
                py_part = py_snapshot.get(section)
                if not isinstance(js_part, dict):
                    errors_msg = f"{name}: JS snapshot missing object section {section!r}"
                    raise ReleaseGateError(errors_msg)
                if not isinstance(py_part, dict):
                    raise ReleaseGateError(
                        f"{name}: Python snapshot missing object section {section!r}"
                    )
                for key in keys:
                    if key not in js_part or key not in py_part:
                        raise ReleaseGateError(
                            f"{name}: missing {section}.{key} in one of the snapshots"
                        )
            zone_count = js_snapshot.get("scenario", {}).get("zones")
            py_zone_count = py_snapshot.get("scenario", {}).get("zones")
            if zone_count != py_zone_count:
                raise ReleaseGateError(f"{name}: zone count differs between snapshots")
            for label, snap in (("JS", js_snapshot), ("Python", py_snapshot)):
                result = snap["result"]
                for key in ("coveredPoint", "servedByPoint", "missedByPoint", "noRouteByPoint"):
                    if len(result[key]) != zone_count:
                        raise ReleaseGateError(
                            f"{name}: {label} {key} length {len(result[key])} != zones {zone_count}"
                        )
                journeys = result["journeyOrigins"]
                if not isinstance(journeys, dict) or any(
                    len(journeys[k]) != zone_count for k in ("journeys", "trips")
                ):
                    raise ReleaseGateError(
                        f"{name}: {label} journeyOrigins length does not match zones"
                    )
                if len(result["periods"]) != 5:
                    raise ReleaseGateError(
                        f"{name}: {label} period result must contain 5 periods"
                    )
    if require and missing:
        raise ReleaseGateError(
            "missing city-level golden snapshots: " + ", ".join(missing)
        )
    return missing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reference",
        type=Path,
        default=Path("tests/fixtures/takt_reference_snapshot.json"),
    )
    parser.add_argument(
        "--city-manifest",
        type=Path,
        default=Path("tests/fixtures/takt_release_city_cases.json"),
    )
    parser.add_argument(
        "--require-city-snapshots",
        action="store_true",
        help="fail unless every city case has checked-in JS/Python golden snapshots",
    )
    parser.add_argument(
        "--check-city-schemas",
        action="store_true",
        help="validate the richer city result schema and point-vector lengths",
    )
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[1]
    try:
        reference = load_json(repo_root / args.reference)
        manifest = load_json(repo_root / args.city_manifest)
        if not isinstance(reference, dict) or not isinstance(manifest, dict):
            raise ReleaseGateError("reference and city manifest must be JSON objects")

        bundle_sha = verify_bundle_provenance(reference, repo_root)
        city_names = validate_city_manifest(manifest, repo_root)
        # City snapshots are generated by the P6 city-parity jobs. Do not
        # compare checked-in snapshots before regeneration, because a stale
        # fixture must not block the job that refreshes it.
        missing: list[str] = []
        if args.require_city_snapshots or args.check_city_schemas:
            missing = verify_city_snapshots(
                manifest,
                repo_root,
                require=args.require_city_snapshots,
                check_schema=args.check_city_schemas,
            )
    except ReleaseGateError as exc:
        print(f"takt-release-gate: FAIL: {exc}", file=sys.stderr)
        return 1

    print(f"takt-release-gate: bundle {bundle_sha}")
    print(f"takt-release-gate: city cases={','.join(city_names)}")
    if missing:
        print(
            "takt-release-gate: city golden snapshots pending="
            + ",".join(missing)
        )
    else:
        print("takt-release-gate: all city golden snapshots verified")
    print("takt-release-gate: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
