#!/usr/bin/env python3
"""Cross-platform local launcher for the canonical Takt JS + JSON P6 pipeline."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "scripts" / "takt_city_python_snapshot.py"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the canonical Takt JS + JSON P6 pipeline locally."
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
    parser.add_argument(
        "--install",
        action="store_true",
        help="Deprecated compatibility flag; the pipeline has no Python runtime dependencies.",
    )
    args = parser.parse_args(argv)

    if not SNAPSHOT.is_file():
        print(f"error: Python pipeline not found: {SNAPSHOT}", file=sys.stderr)
        return 2

    node_path = shutil.which(args.node) if Path(args.node).name == args.node else args.node
    if not node_path or not Path(node_path).is_file() and shutil.which(node_path) is None:
        print(
            f"error: Node.js executable not found: {args.node}. "
            "Install Node.js 20+ and ensure it is in PATH.",
            file=sys.stderr,
        )
        return 2

    if args.install:
        print(
            "P6 local: --install is retained for compatibility; "
            "no NumPy/SciPy/passenger_flow/od installation is required."
        )

    command = [
        sys.executable,
        str(SNAPSHOT),
        f"--city={args.city}",
        f"--output-dir={args.output_dir}",
        f"--node={args.node}",
    ]
    print(f"P6 local: city={args.city}, engine=JS bundle, input=JSON")
    completed = subprocess.run(command, cwd=ROOT)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
