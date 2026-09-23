#!/usr/bin/env python3
"""Cross-platform local launcher for the pinned Takt P6 city snapshot."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "scripts" / "takt_city_python_snapshot.py"
REQUIREMENTS = ROOT / "requirements-p6-local.txt"


def _default_start_method() -> str:
    return "spawn" if os.name == "nt" else "fork"


def _default_workers() -> int:
    return max(1, min(6, os.cpu_count() or 1))


def _set_numeric_thread_limits() -> None:
    for name in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "BLIS_NUM_THREADS",
    ):
        os.environ.setdefault(name, "1")


def _check_imports() -> list[str]:
    missing: list[str] = []
    modules = {
        "numpy": "numpy",
        "scipy": "scipy",
        "networkx": "networkx",
        "shapely": "shapely",
    }
    for label, module in modules.items():
        try:
            __import__(module)
        except ImportError:
            missing.append(label)
    return missing


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the pinned Takt P6 city snapshot locally."
    )
    parser.add_argument(
        "--city",
        default="berlin-v5",
        help="Pinned city case from tests/fixtures/takt_release_city_cases.json",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=_default_workers(),
        help="Maximum P6 worker processes (default: min(6, CPU count)).",
    )
    parser.add_argument(
        "--start-method",
        choices=("auto", "fork", "spawn"),
        default="auto",
        help="Multiprocessing start method (default: auto).",
    )
    parser.add_argument(
        "--install",
        action="store_true",
        help="Install local P6 dependencies before running.",
    )
    args = parser.parse_args()

    if args.workers < 1:
        parser.error("--workers must be >= 1")

    if not SNAPSHOT.is_file():
        print(f"error: snapshot script not found: {SNAPSHOT}", file=sys.stderr)
        return 2

    _set_numeric_thread_limits()
    # The snapshot script lives under scripts/, so make the repository root
    # importable even when Python sets sys.path[0] to the script directory.
    existing_pythonpath = os.environ.get("PYTHONPATH")
    os.environ["PYTHONPATH"] = (
        str(ROOT)
        if not existing_pythonpath
        else os.pathsep.join((str(ROOT), existing_pythonpath))
    )

    start_method = (
        _default_start_method()
        if args.start_method == "auto"
        else args.start_method
    )
    os.environ["TAKT_P6_PROCESSES"] = str(args.workers)
    os.environ["TAKT_P6_MP_START"] = start_method

    missing = _check_imports()
    if missing:
        if not args.install:
            print(
                "Missing Python packages: " + ", ".join(missing),
                file=sys.stderr,
            )
            print(
                f"Install them with: {sys.executable} -m pip install -r {REQUIREMENTS}",
                file=sys.stderr,
            )
            print(
                "Or rerun this command with --install.",
                file=sys.stderr,
            )
            return 2

        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", str(REQUIREMENTS)],
            cwd=ROOT,
            check=True,
        )

    print(
        f"P6 local run: city={args.city}, workers={args.workers}, "
        f"start_method={start_method}, numeric_threads=1"
    )
    command = [
        sys.executable,
        str(SNAPSHOT),
        f"--city={args.city}",
    ]
    completed = subprocess.run(command, cwd=ROOT)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
