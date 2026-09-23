from __future__ import annotations

import json
from pathlib import Path

from scripts.takt_city_python_snapshot import _bundle_path, _cases


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests" / "fixtures" / "takt_release_city_cases.json"


def test_manifest_exposes_all_pinned_city_cases() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert [case["name"] for case in manifest["city_cases"]] == [
        "amsterdam-v8",
        "berlin-v5",
        "hong-kong-v6",
    ]


def test_all_mode_selects_every_city() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert [case["name"] for case in _cases(manifest, None)] == [
        "amsterdam-v8",
        "berlin-v5",
        "hong-kong-v6",
    ]


def test_canonical_bundle_is_present_and_pinned() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    bundle = _bundle_path(manifest)
    assert bundle.is_file()
    assert len(manifest["bundle"]["git_blob_sha"]) == 40


def test_python_orchestrator_does_not_import_legacy_engines() -> None:
    source = (ROOT / "scripts" / "takt_city_python_snapshot.py").read_text(
        encoding="utf-8"
    )
    assert "passenger_flow" not in source
    assert "from od" not in source
    assert "import od" not in source
