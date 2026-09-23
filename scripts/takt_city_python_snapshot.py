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
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests" / "fixtures" / "takt_release_city_cases.json"
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


def _bundle_path(manifest: dict[str, Any]) -> Path:
    bundle = manifest.get("bundle")
    if not isinstance(bundle, dict):
        raise ValueError("city manifest is missing bundle metadata")
    source = bundle.get("source")
    if not isinstance(source, str) or not source:
        raise ValueError("city manifest bundle source is missing")
    path = ROOT / source
    if not path.is_file():
        raise FileNotFoundError(f"Takt JS bundle not found: {path}")
    return path


def run_city(
    *,
    node: str,
    city: str,
    output: Path,
    bundle_path: Path,
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
        f"P6 Python pipeline: city={city}, "
        f"engine=JS bundle, input=JSON, output={output}"
    )
    completed = subprocess.run(command, cwd=ROOT, env=env)
    return completed.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the canonical Takt JS + JSON pipeline through Python."
    )
    parser.add_argument(
        "--city",
        default="berlin-v5",
        help="Pinned city case from tests/fixtures/takt_release_city_cases.json.",
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
    args = parser.parse_args(argv)

    if not JS_PIPELINE.is_file():
        print(f"error: JS pipeline not found: {JS_PIPELINE}", file=sys.stderr)
        return 2
    if not MANIFEST.is_file():
        print(f"error: city manifest not found: {MANIFEST}", file=sys.stderr)
        return 2

    try:
        manifest = load_json(MANIFEST)
        cases = _cases(manifest, args.city)
        bundle_path = _bundle_path(manifest)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    for case in cases:
        name = str(case["name"])
        output = args.output_dir / name / "takt_python_snapshot.json"
        return_code = run_city(
            node=args.node,
            city=name,
            output=output,
            bundle_path=bundle_path,
        )
        if return_code:
            return return_code

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
