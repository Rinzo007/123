from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

from scripts.takt_city_python_snapshot import _bundle_path, _cases


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests" / "fixtures" / "takt_release_city_cases.json"


class TaktJsJsonPipelineTests(unittest.TestCase):
    def test_manifest_exposes_all_pinned_city_cases(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(
            [case["name"] for case in manifest["city_cases"]],
            ["amsterdam-v8", "berlin-v5", "hong-kong-v6"],
        )
        self.assertTrue(
            all("js_snapshot" in case for case in manifest["city_cases"])
        )
        self.assertTrue(
            all("python_snapshot" not in case for case in manifest["city_cases"])
        )

    def test_all_mode_selects_every_city(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(
            [case["name"] for case in _cases(manifest, None)],
            ["amsterdam-v8", "berlin-v5", "hong-kong-v6"],
        )

    def test_canonical_bundle_is_present_and_pinned(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        bundle = _bundle_path(manifest)
        self.assertTrue(bundle.is_file())
        self.assertEqual(len(manifest["bundle"]["git_blob_sha"]), 40)

    def test_python_orchestrator_does_not_import_legacy_engines(self) -> None:
        path = ROOT / "scripts" / "takt_city_python_snapshot.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        imported_modules.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        self.assertFalse(
            any(
                module == "od" or module.startswith("od.")
                or module == "passenger_flow"
                or module.startswith("passenger_flow.")
                for module in imported_modules
            )
        )


if __name__ == "__main__":
    unittest.main()
