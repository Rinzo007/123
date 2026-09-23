from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.takt_city_sources import (
    build_package,
    gravity_od,
    osm_payload_to_lines,
    parse_args,
    purposes_from_od,
)
from scripts.takt_validate_package import validate_manifest


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "scripts" / "bd956ff0a1875604740f.js"


class TaktCitySourcesTests(unittest.TestCase):
    def test_gravity_od_is_non_empty_and_has_no_self_pairs(self) -> None:
        points = [
            [39.00, 51.00, 1000.0, 1000.0],
            [39.05, 51.00, 700.0, 700.0],
            [39.10, 51.00, 500.0, 500.0],
        ]
        rows = gravity_od(points, trips_per_resident=0.2, d0_m=5000.0, k=12)
        self.assertTrue(rows)
        self.assertTrue(all(row[0] != row[1] for row in rows))
        self.assertTrue(all(row[2] > 0 and row[3] > 0 for row in rows))

    def test_osm_relation_payload_becomes_baseline_lines(self) -> None:
        payload = {
            "elements": [
                {"type": "relation", "id": 101, "tags": {"type": "route", "route": "bus", "ref": "5"},
                 "members": [
                     {"type": "way", "ref": 201, "role": ""},
                     {"type": "node", "ref": 301, "role": "stop"},
                     {"type": "node", "ref": 302, "role": "platform"},
                 ]},
                {"type": "way", "id": 201, "geometry": [
                    {"lat": 51.00, "lon": 39.00},
                    {"lat": 51.01, "lon": 39.02},
                    {"lat": 51.02, "lon": 39.04},
                ]},
                {"type": "node", "id": 301, "lat": 51.00, "lon": 39.00, "tags": {"name": "A"}},
                {"type": "node", "id": 302, "lat": 51.02, "lon": 39.04, "tags": {"name": "B"}},
            ]
        }
        lines = osm_payload_to_lines(payload)
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["name"], "5")
        self.assertEqual(lines[0]["mode"], "bus")
        self.assertEqual(len(lines[0]["stops"]), 2)
        self.assertEqual(len(lines[0]["headways"]), 5)

    def test_purpose_payload_is_valid_v2(self) -> None:
        rows = [[0, 1, 10, 900], [1, 0, 8, 900]]
        value = purposes_from_od(rows)
        self.assertEqual(value["v"], 2)
        layer = value["layers"][0]
        self.assertEqual(layer["n"], 2)
        self.assertEqual(len(layer["out"]), 5)
        self.assertEqual(len(layer["ret"]), 5)
        self.assertGreater(len(layer["od"]), 0)

    def test_offline_package_builds_and_validates(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            population = root / "population.geojson"
            routes = root / "routes.geojson"
            output = root / "package"
            population.write_text(
                json.dumps({
                    "type": "FeatureCollection",
                    "features": [
                        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [39.00, 51.00]},
                         "properties": {"population": 1000}},
                        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [39.05, 51.00]},
                         "properties": {"population": 700}},
                        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [39.10, 51.00]},
                         "properties": {"population": 500}},
                    ],
                }),
                encoding="utf-8",
            )
            routes.write_text(
                json.dumps({
                    "type": "FeatureCollection",
                    "features": [{
                        "type": "Feature",
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [[39.00, 51.00], [39.05, 51.00], [39.10, 51.00]],
                        },
                        "properties": {"id": "5", "name": "5", "mode": "bus", "headways": [10, 10, 10, 10, 10]},
                    }],
                }),
                encoding="utf-8",
            )
            args = parse_args([
                "--city", "voronezh-test",
                "--population", str(population),
                "--routes", str(routes),
                "--output-dir", str(output),
                "--bundle", str(BUNDLE),
                "--trips-per-resident", "0.2",
                "--d0-m", "5000",
                "--k", "12",
            ])
            manifest_path = build_package(args)
            manifest = validate_manifest(manifest_path)
            self.assertEqual(manifest["city_cases"][0]["name"], "voronezh-test")
            for filename in ("model.json", "demand.json", "baseline.json", "purposes.json", "bundle.js"):
                self.assertTrue((output / filename).is_file())
            demand = json.loads((output / "demand.json").read_text(encoding="utf-8"))
            self.assertEqual(len(demand["pts"]), 3)
            self.assertTrue(demand["od"])


if __name__ == "__main__":
    unittest.main()
