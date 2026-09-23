#!/usr/bin/env python3
"""Python orchestrator for the canonical Takt JS + JSON city pipeline.

The Python entry point intentionally does not import passenger_flow or od.
It reads the pinned JSON manifest, selects the canonical JS bundle, and delegates
the actual city calculation to scripts/takt_city_browser_snapshot.js.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "tests" / "fixtures" / "takt_release_city_cases.json"
JS_PIPELINE = ROOT / "scripts" / "takt_city_browser_snapshot.js"


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _rooted(path: Path) -> Path:
    return path if path.is_absolute() else ROOT / path


def _cases(manifest: dict[str, Any], city: str | None) -> list[dict[str, Any]]:
    cases = manifest.get("city_cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("city manifest contains no city_cases")
    if city is None:
        return [case for case in cases if isinstance(case, dict)]
    selected = [
        case for case in cases
        if isinstance(case, dict) and case.get("name") == city
    ]
    if not selected:
        raise ValueError(f"unknown city: {city}")
    return selected


def _bundle_path(manifest: dict[str, Any], manifest_path: Path | None = None) -> Path:
    bundle = manifest.get("bundle")
    if not isinstance(bundle, dict):
        raise ValueError("city manifest is missing bundle metadata")
    source = bundle.get("source")
    if not isinstance(source, str) or not source:
        raise ValueError("city manifest bundle source is missing")
    base = manifest_path.parent if manifest_path is not None else ROOT
    path = Path(source)
    if not path.is_absolute():
        path = base / path
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Takt JS bundle not found: {path}")
    return path


def run_city(
    *,
    node: str,
    city: str,
    output: Path,
    bundle_path: Path,
    sync_browser: bool,
) -> int:
    output = _rooted(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["TAKT_BUNDLE_PATH"] = str(bundle_path)

    command = [
        node,
        str(JS_PIPELINE),
        f"--city={city}",
        f"--output={output}",
    ]
    print(
        f"P6 Python orchestration: city={city}, "
        f"engine=JS bundle, input=JSON, output={output}"
    )
    completed = subprocess.run(command, cwd=ROOT, env=env)
    if completed.returncode:
        return completed.returncode

    if sync_browser:
        browser_output = output.parent / "takt_browser_snapshot.json"
        shutil.copyfile(output, browser_output)
        print(f"Synced canonical JS snapshot: {browser_output}")

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the canonical Takt JS + JSON pipeline through Python."
    )
    city_group = parser.add_mutually_exclusive_group()
    city_group.add_argument(
        "--city",
        help="Pinned city case from tests/fixtures/takt_release_city_cases.json.",
    )
    city_group.add_argument(
        "--all",
        action="store_true",
        help="Run every pinned city case from the manifest.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help="City manifest; can be a generated custom-city package manifest.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("tests/fixtures/cities"),
        help="Directory for generated city snapshots.",
    )
    parser.add_argument(
        "--node",
        default="node",
        help="Node.js executable.",
    )
    parser.add_argument(
        "--sync-browser",
        action="store_true",
        help="Also write the canonical JS result to takt_browser_snapshot.json.",
    )
    args = parser.parse_args(argv)

    if not JS_PIPELINE.is_file():
        print(f"error: JS pipeline not found: {JS_PIPELINE}", file=sys.stderr)
        return 2
    manifest_path = _rooted(args.manifest)
    if not manifest_path.is_file():
        print(f"error: city manifest not found: {manifest_path}", file=sys.stderr)
        return 2

    try:
        manifest = load_json(manifest_path)
        bundle_path = _bundle_path(manifest, manifest_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    selected_city = None if args.all else (args.city or "berlin-v5")
    try:
        cases = _cases(manifest, selected_city)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    for case in cases:
        name = str(case["name"])
        output = args.output_dir / name / "takt_result.json"
        return_code = run_city(
            node=args.node,
            city=name,
            output=output,
            bundle_path=bundle_path,
            sync_browser=args.sync_browser,
        )
        if return_code:
            return return_code

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
