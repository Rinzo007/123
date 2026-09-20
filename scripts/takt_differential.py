#!/usr/bin/env python3
"""Compare a Takt JS snapshot with a Python snapshot.

The reference side is expected to be JSON exported from the current Takt
bundle (bd956ff0a1875604740f40.js). The comparator is intentionally pure
stdlib so it can be used in CI, locally, or against browser-exported data.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_ABS_TOL = 1e-9
DEFAULT_REL_TOL = 1e-7


@dataclass(frozen=True)
class Difference:
    path: str
    kind: str
    reference: Any
    actual: Any
    detail: str


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _close(reference: float, actual: float, abs_tol: float, rel_tol: float) -> bool:
    if not (math.isfinite(reference) and math.isfinite(actual)):
        return reference == actual
    return math.isclose(reference, actual, rel_tol=rel_tol, abs_tol=abs_tol)


def _path_matches(path: str, rule: str) -> bool:
    if rule.endswith(".*"):
        prefix = rule[:-2]
        if path.startswith(prefix):
            return True
    if "[*]" in rule:
        prefix, suffix = rule.split("[*]", 1)
        if path.startswith(prefix + "[") and path.endswith(suffix):
            return True
    return path == rule


def _tolerance_for(path: str, rules: dict[str, list[float]] | None) -> tuple[float, float]:
    if not rules:
        return DEFAULT_ABS_TOL, DEFAULT_REL_TOL
    matches = [(rule, value) for rule, value in rules.items() if _path_matches(path, rule)]
    if not matches:
        return DEFAULT_ABS_TOL, DEFAULT_REL_TOL
    _, value = max(matches, key=lambda item: len(item[0]))
    if len(value) != 2:
        raise ValueError(f"Tolerance for {path} must be [abs_tol, rel_tol]")
    return float(value[0]), float(value[1])


def compare_snapshots(
    reference: Any,
    actual: Any,
    *,
    tolerance_rules: dict[str, list[float]] | None = None,
    path: str = "$",
) -> list[Difference]:
    differences: list[Difference] = []
    if _is_number(reference) and _is_number(actual):
        abs_tol, rel_tol = _tolerance_for(path, tolerance_rules)
        if not _close(float(reference), float(actual), abs_tol, rel_tol):
            differences.append(Difference(path, "value", reference, actual, f"outside tolerance abs={abs_tol} rel={rel_tol}"))
        return differences

    if type(reference) is not type(actual):
        differences.append(Difference(path, "type", reference, actual, "different JSON types"))
        return differences

    if isinstance(reference, dict):
        ref_keys = set(reference)
        act_keys = set(actual)
        for key in sorted(ref_keys - act_keys):
            differences.append(Difference(f"{path}.{key}", "missing", reference[key], None, "missing from actual snapshot"))
        for key in sorted(act_keys - ref_keys):
            differences.append(Difference(f"{path}.{key}", "unexpected", None, actual[key], "unexpected in actual snapshot"))
        for key in sorted(ref_keys & act_keys):
            differences.extend(compare_snapshots(reference[key], actual[key], tolerance_rules=tolerance_rules, path=f"{path}.{key}"))
        return differences

    if isinstance(reference, list):
        if len(reference) != len(actual):
            differences.append(Difference(path, "length", len(reference), len(actual), "different array lengths"))
            return differences
        for index, (ref_value, act_value) in enumerate(zip(reference, actual)):
            differences.extend(compare_snapshots(ref_value, act_value, tolerance_rules=tolerance_rules, path=f"{path}[{index}]"))
        return differences

    if reference != actual:
        differences.append(Difference(path, "value", reference, actual, "different scalar values"))
    return differences


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", type=Path, help="JSON snapshot exported from Takt JS")
    parser.add_argument("actual", type=Path, help="JSON snapshot produced by Python")
    parser.add_argument("--tolerance", type=Path, help="Optional JSON map of path patterns to [abs_tol, rel_tol]")
    parser.add_argument("--max-diffs", type=int, default=50)
    parser.add_argument("--section", help="Compare only a top-level JSON section from both snapshots.")
    args = parser.parse_args(argv)

    try:
        reference = load_json(args.reference)
        actual = load_json(args.actual)
        rules = load_json(args.tolerance) if args.tolerance else None
        if args.section:
            reference = reference[args.section]
            actual = actual[args.section]
        differences = compare_snapshots(reference, actual, tolerance_rules=rules)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"takt-differential: error: {exc}", file=sys.stderr)
        return 2

    if not differences:
        print("takt-differential: OK")
        return 0

    print(f"takt-differential: {len(differences)} difference(s)")
    for diff in differences[: max(0, args.max_diffs)]:
        print(f"- {diff.path}: {diff.detail}; reference={diff.reference!r}; actual={diff.actual!r}")
    if len(differences) > args.max_diffs:
        print(f"... {len(differences) - args.max_diffs} more")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())