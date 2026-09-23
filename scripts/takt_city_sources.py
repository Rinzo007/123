#!/usr/bin/env python3
"""Build a Takt city package directly from OSM/WorldPop/OD sources.

The calculation runtime remains JavaScript. This script is an upstream source
adapter only and produces the four JSON inputs consumed by the Takt bundle.

Supported sources:
  * WorldPop GeoTIFF (optional rasterio) + optional GeoJSON boundary;
  * population GeoJSON point/polygon export;
  * OSM public-transport relations from Overpass;
  * baseline JSON / route GeoJSON as an offline alternative;
  * external OD JSON/CSV/Parquet, or a built-in Takt-compatible gravity OD;
  * optional purpose OD JSON/CSV.

Examples:
  python scripts/takt_city_sources.py --city voronezh-v1 \
      --worldpop /data/rus_pop_2025_CN_100m_R2025A_v1.tif \
      --boundary /data/voronezh-boundary.geojson \
      --osm \
      --output-dir /data/voronezh

  python scripts/takt_city_sources.py --city voronezh-v1 \
      --population /data/zones.geojson \
      --routes /data/routes.geojson \
      --od /data/od.csv \
      --output-dir /data/voronezh
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import math
import shutil
import struct
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.takt_build_city_package import blob_sha  # noqa: E402
from scripts.takt_city_input import (  # noqa: E402
    DEFAULT_HEADWAYS,
    MODE_SPEED_KMH,
    InputError,
    load_od as load_od_file,
    load_population as load_population_geojson,
    load_routes as load_routes_geojson,
)

GRID_LON_DEG = 0.01
GRID_LAT_RATIO = 0.62
POP_CUTOFF = 40.0
MIN_DIST_M = 50.0
DEFAULT_D0_M = 5000.0
DEFAULT_K = 12
DEFAULT_TRIPS_PER_RESIDENT = 1.0
CIRCUITY = 1.35
SPEED_MPS = 7.5
BASE_SECONDS = 240.0
OSM_UA = "TaktCitySourceAdapter/1.0 (public transport planning)"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)

ROUTE_TYPE_MAP = {
    "bus": "bus",
    "share_taxi": "bus",
    "trolleybus": "bus",
    "tram": "tram",
    "metro": "metro",
    "train": "rail",
    "light_rail": "rail",
    "rail": "rail",
    "ferry": "water",
}
SKIP_WAY_ROLES = {
    "stop",
    "platform",
    "platform_entry_only",
    "platform_exit_only",
    "from",
    "to",
    "via",
}
STOP_ROLES = {"stop", "platform", "platform_entry_only", "platform_exit_only"}

PURPOSE_PROFILES = {
    "commute": (
        (0.02, 0.70, 0.10, 0.03, 0.15),
        (0.10, 0.10, 0.15, 0.60, 0.05),
    ),
    "edu": (
        (0.04, 0.55, 0.18, 0.08, 0.15),
        (0.10, 0.10, 0.20, 0.50, 0.10),
    ),
    "health": (
        (0.05, 0.25, 0.35, 0.20, 0.15),
        (0.05, 0.20, 0.35, 0.25, 0.15),
    ),
    "shop": (
        (0.02, 0.12, 0.38, 0.28, 0.20),
        (0.04, 0.12, 0.30, 0.34, 0.20),
    ),
    "night": (
        (0.10, 0.02, 0.05, 0.18, 0.65),
        (0.15, 0.02, 0.03, 0.15, 0.65),
    ),
}


class SourceError(ValueError):
    """Invalid or unavailable source data."""


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceError(f"{path}: invalid JSON: {exc}") from exc


def write_json(path: Path, value: Any, *, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if compact:
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    else:
        payload = json.dumps(value, ensure_ascii=False, indent=2)
    path.write_text(payload + "\n", encoding="utf-8")


def request_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    method: str = "GET",
    body: str | None = None,
    timeout: int = 120,
    retries: int = 3,
) -> Any:
    query_url = url
    if params:
        query_url += ("&" if "?" in url else "?") + urlencode(params)
    data = body.encode("utf-8") if body is not None else None
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            request_body = data
            if method.upper() == "POST" and body is not None:
                request_body = urlencode({"data": body}).encode("utf-8")
            req = Request(
                query_url,
                data=request_body,
                method=method,
                headers={
                    "User-Agent": OSM_UA,
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
                },
            )
            with urlopen(req, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
            return json.loads(raw)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(min(8.0, 2.0**attempt))
    raise SourceError(f"HTTP source failed: {url}: {last_error}") from last_error


def _coord(value: Any) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        raise SourceError(f"invalid coordinate: {value!r}")
    lon, lat = float(value[0]), float(value[1])
    if not math.isfinite(lon) or not math.isfinite(lat):
        raise SourceError("non-finite coordinate")
    if not -180.0 <= lon <= 180.0 or not -90.0 <= lat <= 90.0:
        raise SourceError(f"coordinate outside WGS84 range: {value!r}")
    return lon, lat


def _geojson_bounds(value: Any) -> tuple[float, float, float, float] | None:
    if isinstance(value, (list, tuple)):
        if len(value) >= 2 and all(isinstance(v, (int, float)) for v in value[:2]):
            lon, lat = float(value[0]), float(value[1])
            return lon, lat, lon, lat
        bounds = [_geojson_bounds(v) for v in value]
        bounds = [b for b in bounds if b is not None]
        if not bounds:
            return None
        return (
            min(b[0] for b in bounds),
            min(b[1] for b in bounds),
            max(b[2] for b in bounds),
            max(b[3] for b in bounds),
        )
    if isinstance(value, dict):
        if value.get("type") == "FeatureCollection":
            return _geojson_bounds([f.get("geometry") for f in value.get("features", [])])
        if value.get("type") == "Feature":
            return _geojson_bounds(value.get("geometry"))
        if "coordinates" in value:
            return _geojson_bounds(value["coordinates"])
    return None


def load_boundary_geojson(path: Path) -> dict[str, Any]:
    data = load_json(path)
    if data.get("type") == "FeatureCollection":
        features = data.get("features") or []
        for feature in features:
            geometry = feature.get("geometry")
            if isinstance(geometry, dict) and geometry.get("type") in ("Polygon", "MultiPolygon"):
                return geometry
    if data.get("type") == "Feature" and isinstance(data.get("geometry"), dict):
        return data["geometry"]
    if data.get("type") in ("Polygon", "MultiPolygon"):
        return data
    raise SourceError(f"{path}: no Polygon/MultiPolygon geometry found")


def resolve_boundary(
    city: str,
    boundary_path: Path | None,
) -> dict[str, Any] | None:
    if boundary_path is not None:
        return load_boundary_geojson(boundary_path)
    try:
        data = request_json(
            NOMINATIM_URL,
            params={
                "q": city,
                "format": "jsonv2",
                "polygon_geojson": 1,
                "limit": 1,
            },
            timeout=60,
        )
    except SourceError:
        return None
    if not isinstance(data, list) or not data:
        return None
    geojson = data[0].get("geojson")
    if not isinstance(geojson, dict) or geojson.get("type") not in ("Polygon", "MultiPolygon"):
        return None
    return geojson


def boundary_bbox(boundary: dict[str, Any]) -> tuple[float, float, float, float]:
    bounds = _geojson_bounds(boundary)
    if bounds is None:
        raise SourceError("boundary has no coordinates")
    min_lon, min_lat, max_lon, max_lat = bounds
    return min_lat, min_lon, max_lat, max_lon


def parse_bbox(value: str) -> tuple[float, float, float, float]:
    try:
        parts = [float(x.strip()) for x in value.split(",")]
    except ValueError as exc:
        raise SourceError("--bbox must be min_lon,min_lat,max_lon,max_lat") from exc
    if len(parts) != 4:
        raise SourceError("--bbox must be min_lon,min_lat,max_lon,max_lat")
    min_lon, min_lat, max_lon, max_lat = parts
    if not (-180 <= min_lon < max_lon <= 180 and -90 <= min_lat < max_lat <= 90):
        raise SourceError(f"invalid bbox: {value}")
    return min_lat, min_lon, max_lat, max_lon


def fetch_osm_routes(bbox: tuple[float, float, float, float]) -> list[dict[str, Any]]:
    min_lat, min_lon, max_lat, max_lon = bbox
    pattern = "|".join(ROUTE_TYPE_MAP)
    query = (
        '[out:json][timeout:180];('
        f'relation["type"="route"]["route"~"^({pattern})$"]'
        f"({min_lat},{min_lon},{max_lat},{max_lon});"
        f'relation["type"="route_master"]["route_master"~"^({pattern})$"]'
        f"({min_lat},{min_lon},{max_lat},{max_lon});"
        ");(._;>;);out body geom;"
    )
    last: Exception | None = None
    for endpoint in OVERPASS_URLS:
        try:
            payload = request_json(
                endpoint,
                method="POST",
                body=query,
                timeout=210,
                retries=2,
            )
            return osm_payload_to_lines(payload)
        except SourceError as exc:
            last = exc
    raise SourceError(f"all Overpass endpoints failed: {last}") from last


def _stitch_segments(segments: list[list[tuple[float, float]]]) -> list[list[float]]:
    if not segments:
        return []
    unused = [list(s) for s in segments if len(s) >= 2]
    if not unused:
        return []
    chain = list(unused.pop(0))
    while unused:
        extended = False
        head, tail = chain[0], chain[-1]
        for i, seg in enumerate(unused):
            if tail == seg[0]:
                chain.extend(seg[1:])
            elif tail == seg[-1]:
                chain.extend(reversed(seg[:-1]))
            elif head == seg[0]:
                chain = list(reversed(seg[1:])) + chain
            elif head == seg[-1]:
                chain = list(seg[:-1]) + chain
            else:
                continue
            unused.pop(i)
            extended = True
            break
        if not extended:
            chain.extend(unused.pop(0))
    return [[float(lon), float(lat)] for lat, lon in []]


def _stitch_latlon(segments: list[list[tuple[float, float]]]) -> list[list[float]]:
    if not segments:
        return []
    unused = [list(s) for s in segments if len(s) >= 2]
    chain = list(unused.pop(0)) if unused else []
    while unused:
        head, tail = chain[0], chain[-1]
        found = False
        for i, seg in enumerate(unused):
            if tail == seg[0]:
                chain.extend(seg[1:])
            elif tail == seg[-1]:
                chain.extend(reversed(seg[:-1]))
            elif head == seg[0]:
                chain = list(reversed(seg[1:])) + chain
            elif head == seg[-1]:
                chain = list(seg[:-1]) + chain
            else:
                continue
            unused.pop(i)
            found = True
            break
        if not found:
            chain.extend(unused.pop(0))
    return [[float(lon), float(lat)] for lat, lon in chain]


def osm_payload_to_lines(payload: dict[str, Any]) -> list[dict[str, Any]]:
    elements = payload.get("elements") if isinstance(payload, dict) else None
    if not isinstance(elements, list):
        raise SourceError("Overpass response has no elements")
    nodes = {
        int(e["id"]): e
        for e in elements
        if isinstance(e, dict) and e.get("type") == "node" and "id" in e
    }
    ways = {
        int(e["id"]): e
        for e in elements
        if isinstance(e, dict) and e.get("type") == "way" and "id" in e
    }
    lines: list[dict[str, Any]] = []
    for relation in elements:
        if not isinstance(relation, dict) or relation.get("type") != "relation":
            continue
        tags = relation.get("tags") or {}
        route_type = str(tags.get("route") or "").strip().lower()
        route_master = str(tags.get("route_master") or "").strip().lower()
        if route_type not in ROUTE_TYPE_MAP or route_master:
            continue
        mode = ROUTE_TYPE_MAP[route_type]
        members = relation.get("members") or []
        segments: list[list[tuple[float, float]]] = []
        stops: list[list[float]] = []
        for member in members:
            if not isinstance(member, dict):
                continue
            mtype = member.get("type")
            role = str(member.get("role") or "")
            ref = member.get("ref")
            if mtype == "way" and role not in SKIP_WAY_ROLES and ref is not None:
                way = ways.get(int(ref))
                coords = []
                for pt in (way or {}).get("geometry", []) if isinstance(way, dict) else []:
                    if isinstance(pt, dict) and "lat" in pt and "lon" in pt:
                        coords.append((float(pt["lat"]), float(pt["lon"])))
                if len(coords) >= 2:
                    segments.append(coords)
            elif mtype == "node" and role in STOP_ROLES and ref is not None:
                node = nodes.get(int(ref))
                if isinstance(node, dict) and "lat" in node and "lon" in node:
                    stops.append([float(node["lon"]), float(node["lat"])])
        if not segments and not stops:
            continue
        coords = _stitch_latlon(segments)
        if len(stops) < 2:
            if len(coords) < 2:
                continue
            stops = [coords[0], coords[-1]]
        else:
            cleaned: list[list[float]] = []
            for stop in stops:
                if not cleaned or _haversine_m(cleaned[-1], stop) >= 5.0:
                    cleaned.append(stop)
            stops = cleaned
        if len(stops) < 2:
            continue
        ref_name = str(tags.get("ref") or tags.get("name") or relation.get("id") or "").strip()
        line_id = f"osm-{ref_name or relation.get('id')}-{relation.get('id')}"
        speed = MODE_SPEED_KMH.get(mode, 22.0)
        cum = [0]
        for a, b in zip(stops, stops[1:]):
            cum.append(cum[-1] + round(_haversine_m(a, b) / speed * 3.6))
        lines.append(
            {
                "id": line_id,
                "name": ref_name or line_id,
                "mode": mode,
                "headways": list(DEFAULT_HEADWAYS),
                "stops": stops,
                "cumT": cum,
                "trips": 0,
                "source": "osm-overpass",
                "osm_relation_id": relation.get("id"),
                "geometry": coords,
            }
        )
    lines.sort(key=lambda row: (row["mode"], row["name"], str(row["id"])))
    if not lines:
        raise SourceError("Overpass returned no usable public-transport routes")
    return lines


def _haversine_m(a: list[float], b: list[float]) -> float:
    lon1, lat1 = a[0], a[1]
    lon2, lat2 = b[0], b[1]
    lat1r, lat2r = math.radians(lat1), math.radians(lat2)
    dlat, dlon = lat2r - lat1r, math.radians(lon2 - lon1)
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1r) * math.cos(lat2r) * math.sin(dlon / 2) ** 2
    return 6371000.0 * 2.0 * math.asin(math.sqrt(max(0.0, min(1.0, h))))


def load_worldpop(
    path: Path,
    *,
    boundary: dict[str, Any] | None,
    grid_lon_deg: float = GRID_LON_DEG,
    grid_lat_ratio: float = GRID_LAT_RATIO,
) -> list[list[float]]:
    try:
        import numpy as np
        import rasterio
        from rasterio.features import geometry_mask
        from rasterio.windows import from_bounds
        from rasterio.warp import transform
    except ImportError as exc:
        raise SourceError(
            "WorldPop GeoTIFF requires rasterio and numpy: pip install rasterio numpy"
        ) from exc

    with rasterio.open(path) as ds:
        if ds.crs is None:
            raise SourceError(f"{path}: raster has no CRS")
        if boundary is None:
            raise SourceError(
                "WorldPop input requires --boundary (or a successful Nominatim boundary lookup)"
            )
        boundary_raster = boundary
        if boundary is not None and str(ds.crs).upper() not in {"EPSG:4326", "OGC:CRS84"}:
            from rasterio.warp import transform_geom
            boundary_raster = transform_geom("EPSG:4326", ds.crs, boundary)
        if boundary_raster is not None:
            bounds = _geojson_bounds(boundary_raster)
            if bounds is None:
                raise SourceError("boundary has no coordinates")
            min_lon, min_lat, max_lon, max_lat = bounds
            try:
                window = from_bounds(min_lon, min_lat, max_lon, max_lat, ds.transform)
            except (ValueError, TypeError) as exc:
                raise SourceError(f"{path}: cannot build raster window: {exc}") from exc
            window = window.round_offsets().round_lengths()
            band = ds.read(1, window=window, masked=True)
            transform_window = ds.window_transform(window)
        else:
            band = ds.read(1, masked=True)
            transform_window = ds.transform
        rows, cols = band.shape
        rr, cc = np.indices((rows, cols))
        xs, ys = rasterio.transform.xy(transform_window, rr, cc, offset="center")
        xs = np.asarray(xs)
        ys = np.asarray(ys)
        if str(ds.crs).upper() not in {"EPSG:4326", "OGC:CRS84"}:
            flat_x, flat_y = transform(ds.crs, "EPSG:4326", xs.ravel().tolist(), ys.ravel().tolist())
            xs = np.asarray(flat_x).reshape(xs.shape)
            ys = np.asarray(flat_y).reshape(ys.shape)
        values = np.asarray(band.filled(0.0), dtype=float)
        values[~np.isfinite(values)] = 0.0
        values = np.maximum(values, 0.0)
        if boundary is not None:
            mask = geometry_mask(
                [boundary_raster],
                out_shape=values.shape,
                transform=transform_window,
                invert=True,
                all_touched=False,
            )
            values[~mask] = 0.0

        accum: dict[tuple[int, int], list[float]] = {}
        lat_step = grid_lon_deg * grid_lat_ratio
        flat_xs, flat_ys, flat_values = xs.ravel(), ys.ravel(), values.ravel()
        for lon, lat, pop in zip(flat_xs, flat_ys, flat_values):
            pop_f = float(pop)
            if pop_f <= 0.0 or not math.isfinite(float(lon)) or not math.isfinite(float(lat)):
                continue
            key = (math.floor(float(lon) / grid_lon_deg), math.floor(float(lat) / lat_step))
            item = accum.get(key)
            if item is None:
                accum[key] = [pop_f, pop_f * float(lon), pop_f * float(lat)]
            else:
                item[0] += pop_f
                item[1] += pop_f * float(lon)
                item[2] += pop_f * float(lat)
        points = [
            [v[1] / v[0], v[2] / v[0], v[0], 0.0]
            for _, v in sorted(accum.items())
            if v[0] > 0.0
        ]
    if not points:
        raise SourceError(f"{path}: no population cells inside boundary")
    return points


def _round_half_up(value: float) -> int:
    return math.floor(value + 0.5)


def gravity_od(
    points: list[list[float]],
    *,
    trips_per_resident: float = DEFAULT_TRIPS_PER_RESIDENT,
    d0_m: float = DEFAULT_D0_M,
    k: int = DEFAULT_K,
    grid_lon_deg: float = GRID_LON_DEG,
    grid_lat_ratio: float = GRID_LAT_RATIO,
) -> list[list[int]]:
    if trips_per_resident <= 0 or d0_m <= 0 or k <= 0:
        raise SourceError("gravity parameters must be positive")
    if not points:
        raise SourceError("gravity requires population points")
    mean_lat = sum(float(p[1]) for p in points) / len(points)
    lon_m_per_deg = 111320.0 * math.cos(math.radians(mean_lat))
    lat_m_per_deg = 110540.0
    attract = [max(0.0, float(p[3] if len(p) > 3 and p[3] > 0 else p[2])) for p in points]
    cells: dict[tuple[int, int], list[float]] = {}
    for idx, value in enumerate(attract):
        if value <= 0:
            continue
        key = (
            math.floor(float(points[idx][0]) / grid_lon_deg),
            math.floor(float(points[idx][1]) / (grid_lon_deg * grid_lat_ratio)),
        )
        item = cells.get(key)
        if item is None:
            cells[key] = [value, float(points[idx][0]), float(points[idx][1]), float(idx), value]
        else:
            item[0] += value
            if value > item[4]:
                item[3] = float(idx)
                item[4] = value
    if not cells:
        raise SourceError("gravity attraction is zero")
    rows_by_cell = {key: value for key, value in cells.items()}
    half_bands = (0.0, d0_m, 2.5 * d0_m, 6.0 * d0_m, math.inf)
    cell_lon_radius = max(1, math.ceil((8.0 * d0_m) / max(lon_m_per_deg * grid_lon_deg, 1.0)))
    cell_lat_radius = max(1, math.ceil((8.0 * d0_m) / (lat_m_per_deg * grid_lon_deg * grid_lat_ratio)))
    result: list[list[int]] = []
    for origin, point in enumerate(points):
        pop = max(0.0, float(point[2]))
        if pop < POP_CUTOFF:
            continue
        target_trips = pop * trips_per_resident
        if target_trips <= 0:
            continue
        ox, oy = float(point[0]), float(point[1])
        cx = math.floor(ox / grid_lon_deg)
        cy = math.floor(oy / (grid_lon_deg * grid_lat_ratio))
        candidates: list[tuple[int, float, float]] = []
        for dx in range(-cell_lon_radius, cell_lon_radius + 1):
            for dy in range(-cell_lat_radius, cell_lat_radius + 1):
                cell = rows_by_cell.get((cx + dx, cy + dy))
                if cell is None:
                    continue
                attr, lon, lat, best_idx_f, _best_attr = cell
                distance = math.hypot((lon - ox) * lon_m_per_deg, (lat - oy) * lat_m_per_deg)
                if distance < MIN_DIST_M or distance > 8.0 * d0_m:
                    continue
                if int(best_idx_f) == origin:
                    continue
                ev = attr * math.exp(-distance / d0_m)
                if ev <= 0:
                    continue
                candidates.append((int(best_idx_f), distance, ev))
        if not candidates:
            continue
        total_ev = sum(v[2] for v in candidates)
        if total_ev <= 0:
            continue
        for band_index in range(len(half_bands) - 1):
            lo, hi = half_bands[band_index], half_bands[band_index + 1]
            in_band = [v for v in candidates if lo <= v[1] < hi]
            if not in_band:
                continue
            in_band.sort(key=lambda v: (-v[2], v[0]))
            width = max(1, _round_half_up(k / 4.0))
            ranked = in_band[:width]
            band_ev = sum(v[2] for v in in_band)
            ranked_ev = sum(v[2] for v in ranked)
            if band_ev <= 0 or ranked_ev <= 0:
                continue
            band_trips = target_trips * band_ev / total_ev
            carry = 0.0
            for dest, distance, weight in ranked:
                value = band_trips * weight / ranked_ev + carry
                trips = _round_half_up(value)
                carry = value - trips
                if trips <= 0:
                    continue
                seconds = _round_half_up(distance * CIRCUITY / SPEED_MPS + BASE_SECONDS)
                result.append([origin, dest, trips, seconds])
    if not result:
        raise SourceError(
            "gravity produced no OD pairs; increase d0/trips-per-resident or verify point spacing"
        )
    return result


def _f32_b64(rows: list[list[float]]) -> str:
    raw = b"".join(struct.pack("<4f", *(float(v) for v in row)) for row in rows)
    return base64.b64encode(raw).decode("ascii")


def purposes_from_od(
    rows: list[list[int]],
    *,
    purpose_key: str = "commute",
) -> dict[str, Any]:
    if not rows:
        return {"v": 2, "layers": [], "commuteBaseT": []}
    out, ret = PURPOSE_PROFILES.get(purpose_key, PURPOSE_PROFILES["commute"])
    pairs = [[float(a), float(b), float(c), float(t)] for a, b, c, t in rows if c > 0]
    if not pairs:
        return {"v": 2, "layers": [], "commuteBaseT": []}
    return {
        "v": 2,
        "layers": [{
            "t": purpose_key,
            "n": len(pairs),
            "out": list(out),
            "ret": list(ret),
            "od": _f32_b64(pairs),
        }],
        "commuteBaseT": [],
    }


def load_purpose_input(path: Path) -> dict[str, Any]:
    if path.suffix.lower() == ".json":
        value = load_json(path)
        if not isinstance(value, dict) or not isinstance(value.get("layers"), list):
            raise SourceError(f"{path}: expected Takt purposes JSON with layers")
        return value
    if path.suffix.lower() == ".csv":
        rows_by_key: dict[str, list[list[int]]] = {}
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                try:
                    key = str(row.get("purpose") or row.get("key") or "commute").strip().lower()
                    rows_by_key.setdefault(key, []).append([
                        int(float(row["origin"])),
                        int(float(row["destination"])),
                        int(round(float(row["trips"]))),
                        int(round(float(row.get("seconds") or row.get("time_s") or 0))),
                    ])
                except (KeyError, TypeError, ValueError):
                    continue
        layers = []
        for key, rows in sorted(rows_by_key.items()):
            if not rows:
                continue
            out, ret = PURPOSE_PROFILES.get(key, PURPOSE_PROFILES["commute"])
            pairs = [[float(a), float(b), float(c), float(t)] for a, b, c, t in rows if c > 0]
            if pairs:
                layers.append({
                    "t": key,
                    "n": len(pairs),
                    "out": list(out),
                    "ret": list(ret),
                    "od": _f32_b64(pairs),
                })
        return {"v": 2, "layers": layers, "commuteBaseT": []}
    raise SourceError(f"{path}: unsupported purpose format")


def _validate_lines(lines: list[dict[str, Any]]) -> None:
    if not lines:
        raise SourceError("baseline contains no routes")
    for i, line in enumerate(lines):
        stops = line.get("stops")
        if not isinstance(stops, list) or len(stops) < 2:
            raise SourceError(f"baseline line {i}: fewer than two stops")
        for stop in stops:
            _coord(stop)


def write_baseline(lines: list[dict[str, Any]], path: Path) -> None:
    payload = {
        "generated": "OSM/GTFS source adapter",
        "lines": lines,
    }
    write_json(path, payload, compact=True)


def copy_or_default_model(city: str, model_path: Path | None, output: Path) -> None:
    if model_path is not None:
        value = load_json(model_path)
        if not isinstance(value, dict):
            raise SourceError(f"{model_path}: model must be a JSON object")
        write_json(output, value, compact=True)
        return
    model = {
        "city": city,
        "mobility": {
            "noCar": 0.5,
            "twoWheelShare": 0.0,
            "twoWheelSpeed": 4.2,
            "twoWheelReachM": 8000,
            "twoWheelPerKm": 0.03,
        },
        "rest": {
            "baseSpeed": 3.6,
            "contSpeed": 5.0,
            "accessS": 420,
            "waitS": 240,
            "circuity": 1.3,
        },
        "car": {"costPerKm": 0.25, "parkEur": 1.5, "parkingS": 240},
        "votSPerEur": 360,
        "source": "takt-city-sources default",
    }
    write_json(output, model, compact=True)


def create_manifest(
    city: str,
    version: str,
    output_dir: Path,
    bundle_path: Path,
) -> Path:
    manifest = {
        "schema": 1,
        "bundle": {
            "source": "bundle.js",
            "git_blob_sha": blob_sha(bundle_path),
        },
        "city_cases": [{
            "name": city,
            "version": version,
            "model": "model.json",
            "demand": "demand.json",
            "baseline": "baseline.json",
            "purposes": "purposes.json",
            "git_blob_sha": {
                "model": blob_sha(output_dir / "model.json"),
                "demand": blob_sha(output_dir / "demand.json"),
                "baseline": blob_sha(output_dir / "baseline.json"),
                "purposes": blob_sha(output_dir / "purposes.json"),
            },
            "golden_scenario": {
                "max_od_pairs": 3000,
                "max_purpose_od_pairs": 1500,
                "max_lines": 60,
                "selection": "top trips descending; ties by original row index; lines ranked by coarse endpoint-cell coverage then id",
            },
        }],
        "sources": {},
    }
    path = output_dir / "takt_city_manifest.json"
    write_json(path, manifest)
    return path


def prepare_population(
    *,
    population_path: Path | None,
    worldpop_path: Path | None,
    boundary: dict[str, Any] | None,
    grid_lon_deg: float,
) -> list[list[float]]:
    if worldpop_path is not None:
        return load_worldpop(
            worldpop_path,
            boundary=boundary,
            grid_lon_deg=grid_lon_deg,
        )
    if population_path is None:
        raise SourceError("provide --population or --worldpop")
    _ids, points = load_population_geojson(population_path)
    return points


def prepare_routes(
    *,
    routes_path: Path | None,
    use_osm: bool,
    bbox: tuple[float, float, float, float] | None,
) -> list[dict[str, Any]]:
    if routes_path is not None:
        return load_routes_geojson(routes_path)
    if not use_osm:
        raise SourceError("provide --routes or --osm")
    if bbox is None:
        raise SourceError("OSM routes require --bbox or a resolvable city boundary")
    return fetch_osm_routes(bbox)


def build_package(args: argparse.Namespace) -> Path:
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if args.worldpop and args.population:
        raise SourceError("--worldpop and --population are mutually exclusive")
    if args.routes and args.osm:
        raise SourceError("--routes and --osm are mutually exclusive")

    boundary = resolve_boundary(args.place or args.city, args.boundary) if (args.worldpop or args.osm or args.boundary) else None
    bbox = parse_bbox(args.bbox) if args.bbox else (boundary_bbox(boundary) if boundary else None)

    points = prepare_population(
        population_path=args.population,
        worldpop_path=args.worldpop,
        boundary=boundary,
        grid_lon_deg=args.grid_lon_deg,
    )

    if args.od:
        zone_ids = list(range(len(points)))
        od_rows = load_od_file(args.od, None)
    else:
        od_rows = gravity_od(
            points,
            trips_per_resident=args.trips_per_resident,
            d0_m=args.d0_m,
            k=args.k,
            grid_lon_deg=args.grid_lon_deg,
        )

    lines = prepare_routes(routes_path=args.routes, use_osm=args.osm, bbox=bbox)
    _validate_lines(lines)

    if args.purposes:
        purposes = load_purpose_input(args.purposes)
    else:
        purposes = {"v": 2, "layers": [], "commuteBaseT": []}

    bundle_source = args.bundle.expanduser().resolve()
    if not bundle_source.is_file():
        raise SourceError(f"bundle not found: {bundle_source}")
    bundle_target = output_dir / "bundle.js"
    shutil.copy2(bundle_source, bundle_target)

    demand = {
        "city": args.city,
        "source": {
            "population": "WorldPop" if args.worldpop else str(args.population),
            "od": "external" if args.od else "gravity",
        },
        "pts": points,
        "od": od_rows,
    }
    baseline = {"generated": "OSM/GTFS source adapter", "lines": lines}
    write_json(output_dir / "demand.json", demand, compact=True)
    write_json(output_dir / "baseline.json", baseline, compact=True)
    write_json(output_dir / "purposes.json", purposes, compact=True)
    copy_or_default_model(args.city, args.model, output_dir / "model.json")

    manifest_path = create_manifest(args.city, args.version, output_dir, bundle_target)
    manifest = load_json(manifest_path)
    manifest["sources"] = {
        "boundary": str(args.boundary) if args.boundary else ("nominatim" if boundary else None),
        "population": str(args.worldpop or args.population) if (args.worldpop or args.population) else None,
        "routes": str(args.routes) if args.routes else ("overpass" if args.osm else None),
        "od": str(args.od) if args.od else "gravity",
        "purposes": str(args.purposes) if args.purposes else None,
    }
    if bbox:
        manifest["sources"]["bbox"] = list(bbox)
    write_json(manifest_path, manifest)

    print(f"takt-city-sources: package={output_dir}")
    print(f"  points={len(points):,} od_pairs={len(od_rows):,} lines={len(lines):,}")
    print(f"  manifest={manifest_path}")
    return manifest_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--city", required=True)
    parser.add_argument("--place", help="OSM/Nominatim place name; defaults to --city.")
    parser.add_argument("--version", default="custom")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--bundle", type=Path, default=ROOT / "scripts" / "bd956ff0a1875604740f.js")
    population = parser.add_mutually_exclusive_group(required=False)
    population.add_argument("--population", type=Path, help="Population/zone GeoJSON.")
    population.add_argument("--worldpop", type=Path, help="WorldPop GeoTIFF.")
    routes = parser.add_mutually_exclusive_group(required=False)
    routes.add_argument("--routes", type=Path, help="Offline baseline JSON/route GeoJSON.")
    routes.add_argument("--osm", action="store_true", help="Fetch public transport relations from Overpass.")
    parser.add_argument("--boundary", type=Path, help="GeoJSON Polygon/MultiPolygon; otherwise Nominatim is tried.")
    parser.add_argument("--bbox", help="Overpass bbox: min_lon,min_lat,max_lon,max_lat.")
    parser.add_argument("--od", type=Path, help="External OD JSON/CSV/Parquet.")
    parser.add_argument("--purposes", type=Path, help="Takt purpose JSON or purpose OD CSV.")
    parser.add_argument("--model", type=Path)
    parser.add_argument("--grid-lon-deg", type=float, default=GRID_LON_DEG)
    parser.add_argument("--trips-per-resident", type=float, default=DEFAULT_TRIPS_PER_RESIDENT)
    parser.add_argument("--d0-m", type=float, default=DEFAULT_D0_M)
    parser.add_argument("--k", type=int, default=DEFAULT_K)
    parser.add_argument("--run", action="store_true", help="Run the JS Takt engine after building the package.")
    parser.add_argument("--node", default="node")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        manifest_path = build_package(args)
        validator = ROOT / "scripts" / "takt_validate_package.py"
        import subprocess

        subprocess.run(
            [sys.executable, str(validator), str(manifest_path)],
            check=True,
        )
        if args.run:
            runner = ROOT / "scripts" / "takt_city_python_snapshot.py"
            subprocess.run(
                [
                    sys.executable,
                    str(runner),
                    "--manifest",
                    str(manifest_path),
                    "--city",
                    args.city,
                    "--output-dir",
                    str(Path(args.output_dir).resolve() / "results"),
                    "--node",
                    args.node,
                ],
                check=True,
            )
    except (SourceError, InputError, OSError, ValueError) as exc:
        print(f"takt-city-sources: FAIL: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
