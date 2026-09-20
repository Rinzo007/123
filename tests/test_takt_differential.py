from __future__ import annotations

from scripts.takt_differential import compare_snapshots


def test_equal_snapshot_is_clean() -> None:
    reference = {"a": 1.0, "nested": [2, {"x": 3.0}]}
    actual = {"a": 1.0 + 1e-10, "nested": [2, {"x": 3.0 + 1e-10}]}
    assert compare_snapshots(reference, actual) == []


def test_differential_reports_missing_and_value_changes() -> None:
    differences = compare_snapshots(
        {"a": 1.0, "missing": 2},
        {"a": 1.5, "extra": 3},
    )
    assert {difference.kind for difference in differences} == {"value", "missing", "unexpected"}


def test_differential_allows_field_specific_tolerance() -> None:
    differences = compare_snapshots(
        {"geometry": {"length": 100.0}, "strict": 2.0},
        {"geometry": {"length": 100.001}, "strict": 2.00001},
        tolerance_rules={"$.geometry.*": [0.01, 0.0]},
    )
    assert len(differences) == 1
    assert differences[0].path == "$.strict"

def test_differential_matches_array_descendants_with_dot_wildcard() -> None:
    differences = compare_snapshots(
        {"values": [1.0]},
        {"values": [1.001]},
        tolerance_rules={"$.values.*": [0.01, 0.0]},
    )
    assert differences == []