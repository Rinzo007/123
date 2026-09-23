#!/usr/bin/env python3
"""Normalize OSM, WorldPop-derived and OD exports into a canonical Takt city package.

The adapter does not download source data. It converts existing exports into:
  model.json, demand.json, baseline.json, purposes.json, manifest.json

Population input: GeoJSON Point features or polygon zones with population fields.
OD input: JSON, CSV, or Parquet (Parquet needs pandas and a parquet engine).
Routes input: baseline JSON with lines or GeoJSON LineString/MultiLineString features.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUNDLE = ROOT / "scripts" / "bd956ff0a1875604740f.js"
PERIOD_COUNT = 5
DEFAULT_HEADWAYS = (10.0, 10.0, 10.0, 10.0, 10.0)
MODE_SPEED_KMH = {"bus": 22.0, "trolleybus": 20.0, "tram": 24.0, "metro": 36.0, "rail": 48.0, "train": 48.0}

class InputError(ValueError):
    pass

def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InputError(f"{path}: cannot read JSON: {exc}") from exc

def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def git_blob_sha(path: Path) -> str:
    data = path.read_bytes()
    return hashlib.sha1(f"blob {len(data)}\0".encode("ascii") + data).hexdigest()

def finite(value: Any, default: float = 0.0) -> float:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default

def haversine_m(a: list[float], b: list[float]) -> float:
    lon1, lat1 = map(math.radians, a)
    lon2, lat2 = map(math.radians, b)
    dl, dp = lon2 - lon1, lat2 - lat1
    h = math.sin(dp / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dl / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(max(0.0, min(1.0, h))))

def props(feature: dict[str, Any]) -> dict[str, Any]:
    value = feature.get("properties")
    return value if isinstance(value, dict) else {}

def load_population(path: Path) -> tuple[list[int], list[list[float]]]:
    data = load_json(path)
    features = data.get("features") if isinstance(data, dict) else None
    if not isinstance(features, list):
        raise InputError(f"{path}: expected GeoJSON FeatureCollection")
    point_rows = []
    zone_rows = []
    for i, feature in enumerate(features):
        if not isinstance(feature, dict):
            continue
        geom = feature.get("geometry") or {}
        p = props(feature)
        if geom.get("type") == "Point":
            c = geom.get("coordinates")
            if isinstance(c, list) and len(c) >= 2:
                pop = finite(p.get("pop", p.get("population", p.get("production", 0))))
                jobs = finite(p.get("jobs", p.get("employment", p.get("attraction", 0))))
                point_rows.append((i, [finite(c[0]), finite(c[1]), pop, jobs]))
        elif geom.get("type") in ("Polygon", "MultiPolygon"):
            coords = geom.get("coordinates")
            ring = None
            if geom.get("type") == "Polygon" and coords and isinstance(coords[0], list):
                ring = coords[0]
            elif geom.get("type") == "MultiPolygon" and coords and coords[0] and isinstance(coords[0][0], list):
                ring = coords[0][0]
            if ring:
                pts = [[finite(x[0]), finite(x[1])] for x in ring if isinstance(x, list) and len(x) >= 2]
                if pts:
                    zid = int(float(p.get("zone_id", p.get("id", i))))
                    lon = sum(x[0] for x in pts) / len(pts)
                    lat = sum(x[1] for x in pts) / len(pts)
                    pop = finite(p.get("population", p.get("pop", p.get("production", 0))))
                    jobs = finite(p.get("jobs", p.get("employment", p.get("attraction", 0))))
                    zone_rows.append((zid, [lon, lat, pop, jobs]))
    if point_rows:
        point_rows.sort(key=lambda x: x[0])
        return list(range(len(point_rows))), [row for _, row in point_rows]
    if zone_rows:
        zone_rows.sort(key=lambda x: x[0])
        return [z for z, _ in zone_rows], [row for _, row in zone_rows]
    raise InputError(f"{path}: no usable population or zone features")

def load_od(path: Path, zone_map: dict[int, int] | None) -> list[list[int]]:
    rows: list[Any] = []
    suffix = path.suffix.lower()
    if suffix == ".json":
        data = load_json(path)
        raw = data.get("od") if isinstance(data, dict) else data
        if not isinstance(raw, list):
            raise InputError(f"{path}: expected an OD array")
        rows = raw
    elif suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    elif suffix in {".parquet", ".pq"}:
        try:
            import pandas as pd
            frame = pd.read_parquet(path)
        except Exception as exc:
            raise InputError(f"{path}: Parquet requires pandas plus a parquet engine") from exc
        rows = frame.to_dict("records")
    else:
        raise InputError(f"{path}: unsupported OD format {suffix}")
    result = []
    for row in rows:
        if isinstance(row, dict):
            oi = row.get("origin", row.get("orig_zone", row.get("from")))
            di = row.get("destination", row.get("dest_zone", row.get("to")))
            trips = row.get("trips", 0)
            seconds = row.get("seconds", row.get("time_s", 0))
        elif isinstance(row, (list, tuple)) and len(row) >= 3:
            oi, di, trips = row[:3]
            seconds = row[3] if len(row) > 3 else 0
        else:
            continue
        try:
            oi, di = int(float(oi)), int(float(di))
            trips = int(round(max(0.0, float(trips))))
            seconds = int(round(max(0.0, float(seconds))))
        except (TypeError, ValueError):
            continue
        if zone_map is not None:
            oi, di = zone_map.get(oi, -1), zone_map.get(di, -1)
        if oi >= 0 and di >= 0 and oi != di and trips > 0:
            result.append([oi, di, trips, seconds])
    if not result:
        raise InputError(f"{path}: no valid OD pairs")
    return result

def _default_model(city: str) -> dict[str, Any]:
    return {"city": city, "mobility": {"noCar": 0.5, "twoWheelShare": 0.0, "twoWheelSpeed": 4.2, "twoWheelReachM": 8000, "twoWheelPerKm": 0.03}, "rest": {"baseSpeed": 3.6, "contSpeed": 5.0, "accessS": 420, "waitS": 240, "circuity": 1.3}, "car": {"costPerKm": 0.25, "parkEur": 1.5, "parkingS": 240}, "votSPerEur": 360}

def load_routes(path: Path) -> list[dict[str, Any]]:
    data = load_json(path)
    if isinstance(data, dict) and isinstance(data.get("lines"), list):
        return [dict(x) for x in data["lines"] if isinstance(x, dict)]
    features = data.get("features") if isinstance(data, dict) else None
    if not isinstance(features, list):
        raise InputError(f"{path}: expected baseline JSON or route GeoJSON")
    lines = []
    for i, feature in enumerate(features):
        if not isinstance(feature, dict) or feature.get("geometry", {}).get("type") not in ("LineString", "MultiLineString"):
            continue
        geom = feature["geometry"]
        coords = geom.get("coordinates")
        if geom["type"] == "MultiLineString":
            coords = [pt for part in coords or [] for pt in part]
        if not isinstance(coords, list) or len(coords) < 2:
            continue
        p = props(feature)
        stops = p.get("stops")
        if not isinstance(stops, list) or len(stops) < 2:
            stops = coords
        stops = [[finite(x[0]), finite(x[1])] for x in stops if isinstance(x, list) and len(x) >= 2]
        if len(stops) < 2:
            continue
        mode = str(p.get("mode", p.get("route_type", "bus"))).strip().lower()
        mode = {"trolleybus": "bus", "train": "rail", "light_rail": "rail"}.get(mode, mode)
        hw = p.get("headways", DEFAULT_HEADWAYS)
        if not isinstance(hw, list) or len(hw) != PERIOD_COUNT:
            hw = list(DEFAULT_HEADWAYS)
        hw = [max(0.1, finite(x, 10.0)) for x in hw]
        speed = max(1.0, finite(p.get("speed_kmh"), MODE_SPEED_KMH.get(mode, 22.0)))
        cum = [0]
        for a, b in zip(stops, stops[1:]):
            cum.append(cum[-1] + int(round(haversine_m(a, b) / speed * 3.6)))
        line_id = str(p.get("id", p.get("route_id", i + 1)))
        lines.append({"id": line_id, "name": str(p.get("name", line_id)), "mode": mode, "headways": hw, "stops": stops, "cumT": p.get("cumT", cum), "trips": int(round(max(0.0, finite(p.get("trips"), 0.0))))})
    if not lines:
        raise InputError(f"{path}: no usable route features")
    return lines

def build_case(city: str, population: Path, routes: Path, od: Path, output_dir: Path, model: Path | None, purposes: Path | None, version: str) -> Path:
    zone_ids, points = load_population(population)
    zone_map = {z: i for i, z in enumerate(zone_ids)} if zone_ids != list(range(len(zone_ids))) else None
    od_rows = load_od(od, zone_map)
    demand = {"city": city, "source": "OSM/WorldPop/OD adapter", "pts": points, "od": od_rows}
    baseline = {"generated": "generic OSM route export", "lines": load_routes(routes)}
    model_data = load_json(model) if model else _default_model(city)
    purposes_data = load_json(purposes) if purposes else {"v": 2, "layers": []}
    if not isinstance(model_data, dict) or not isinstance(purposes_data, dict):
        raise InputError("model and purposes must be JSON objects")
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = {"model.json": model_data, "demand.json": demand, "baseline.json": baseline, "purposes.json": purposes_data}
    for name, value in outputs.items():
        write_json(output_dir / name, value)
    rel = lambda p: str(p.relative_to(ROOT)).replace("\\", "/")
    manifest = {"schema": 1, "bundle": {"source": "scripts/bd956ff0a1875604740f.js", "git_blob_sha": git_blob_sha(DEFAULT_BUNDLE)}, "city_cases": [{"name": city, "version": version, "model": rel(output_dir / "model.json"), "demand": rel(output_dir / "demand.json"), "baseline": rel(output_dir / "baseline.json"), "purposes": rel(output_dir / "purposes.json"), "git_blob_sha": {n: git_blob_sha(output_dir / n) for n in outputs}, "golden_scenario": {"max_od_pairs": 3000, "max_purpose_od_pairs": 1500, "max_lines": 60, "selection": "top trips descending; ties by original row index; lines ranked by coarse endpoint-cell coverage then id"}}], "sources": {"population": str(population), "routes": str(routes), "od": str(od)}}
    manifest_path = output_dir / "manifest.json"
    write_json(manifest_path, manifest)
    return manifest_path

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--city", required=True)
    parser.add_argument("--population", type=Path, required=True)
    parser.add_argument("--routes", type=Path, required=True)
    parser.add_argument("--od", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", type=Path)
    parser.add_argument("--purposes", type=Path)
    parser.add_argument("--version", default="custom")
    args = parser.parse_args(argv)
    try:
        out = build_case(args.city, args.population, args.routes, args.od, args.output_dir, args.model, args.purposes, args.version)
    except (InputError, OSError, ValueError) as exc:
        print(f"takt-city-input: FAIL: {exc}")
        return 1
    print(f"takt-city-input: OK: {out}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())