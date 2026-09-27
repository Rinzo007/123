from __future__ import annotations

import hashlib
import json
import shutil
import struct
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping

from .binary_pack import (
    encode_buildings_bin,
    encode_demand_bin,
    encode_stops_bin,
    encode_streets_bin,
    encode_water_bin,
)
from .city import DemandZone
from .city_demand import CityDemandConfig
from .geo import Point
from .places import CityPlace, aggregate_place_attractions
from .projection import project_local_point_wgs84, project_wgs84_point
from .overture import OvertureSource, OvertureUrbanProvider
from .overture_network import OvertureNetworkProvider
from .reference_demand import build_daily_demand
from .zones import generate_grid_zones

PACK_SCHEMA_VERSION = 1
TKBL_HEADER_BYTES = 16
REQUIRED_PACK_FILES = frozenset(
    {
        "model.json",
        "streets.json",
        "streets.bin",
        "stops.bin",
        "zones.bin",
        "demand.bin",
        "buildings.bin",
        "water.bin",
    }
)


@dataclass(frozen=True, slots=True)
class CityPackFile:
    name: str
    data: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


@dataclass(frozen=True, slots=True)
class CityPackManifest:
    city: str
    version: str
    sha256: str
    files: dict[str, dict[str, int | str]]
    total_bytes: int
    schema_version: int = PACK_SCHEMA_VERSION
    source: str = "overture"
    release: str = ""

    def to_dict(self) -> dict:
        return {
            "city": self.city,
            "version": self.version,
            "sha256": self.sha256,
            "files": self.files,
            "totalBytes": self.total_bytes,
            "schemaVersion": self.schema_version,
            "source": self.source,
            "release": self.release,
        }


class CityPackError(ValueError):
    """Raised when a pack on disk deviates from its manifest."""


class CityPackLoadError(CityPackError):
    """Raised when a stored pack is unusable; ``partial`` marks recoverable
    damage (missing files / foreign files) that re-packing can fix."""

    def __init__(self, message: str, *, partial: bool = False) -> None:
        super().__init__(message)
        self.partial = partial


def encode_tkbl(lines: list[list[tuple[float, float]]]) -> bytes:
    point_count = sum(len(line) for line in lines)
    offsets_bytes = (len(lines) + 1) * 4
    total = TKBL_HEADER_BYTES + offsets_bytes + point_count * 8
    output = bytearray(total)
    output[0:4] = b"TKBL"
    struct.pack_into("<HHII", output, 4, 1, 0, len(lines), point_count)

    offset = TKBL_HEADER_BYTES
    point_offset = 0
    for line in lines:
        struct.pack_into("<I", output, offset, point_offset)
        offset += 4
        point_offset += len(line)
    struct.pack_into("<I", output, offset, point_offset)
    offset = TKBL_HEADER_BYTES + offsets_bytes

    for line in lines:
        for lon, lat in line:
            struct.pack_into(
                "<ii",
                output,
                offset,
                round(lon * 1_000_000),
                round(lat * 1_000_000),
            )
            offset += 8
    return bytes(output)


def pack_city_files(
    city: str,
    version: str,
    files: Mapping[str, bytes],
    *,
    release: str = "",
) -> tuple[CityPackManifest, dict[str, bytes]]:
    normalized = {
        name: bytes(data)
        for name, data in sorted(files.items())
        if name and not name.endswith("/") and name != "manifest.json"
    }
    missing = REQUIRED_PACK_FILES - set(normalized)
    if missing:
        raise CityPackError(
            "City pack is missing required files: " + ", ".join(sorted(missing))
        )
    extra = set(normalized) - REQUIRED_PACK_FILES
    if extra:
        raise CityPackError(
            "City pack contains files outside the v1 manifest: "
            + ", ".join(sorted(extra))
        )

    payload = bytearray()
    manifest_files: dict[str, dict[str, int | str]] = {}
    for name, data in normalized.items():
        encoded_name = name.encode("utf-8")
        payload.extend(struct.pack("<I", len(encoded_name)))
        payload.extend(encoded_name)
        payload.extend(data)
        manifest_files[name] = {
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }

    manifest = CityPackManifest(
        city=city,
        version=version,
        sha256=hashlib.sha256(payload).hexdigest(),
        files=manifest_files,
        total_bytes=sum(len(data) for data in normalized.values()),
        release=release,
    )
    return manifest, normalized


def write_city_pack(
    output_dir: str | Path,
    city: str,
    version: str,
    files: Mapping[str, bytes],
    *,
    release: str = "",
) -> CityPackManifest:
    """Atomically write a pack: stage into a temp dir, verify, then swap.

    A partially written pack is never visible under the target path.
    """
    manifest, normalized = pack_city_files(city, version, files, release=release)
    destination = Path(output_dir)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{destination.name}.staging-",
            dir=str(destination.parent),
        )
    )
    published = False
    try:
        for name, data in normalized.items():
            path = staging / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        (staging / "manifest.json").write_text(
            json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        verify_city_pack(staging)
        if destination.exists():
            if destination.is_file():
                raise CityPackError(f"Pack target is a file: {destination}")
            shutil.rmtree(destination)
        staging.replace(destination)
        published = True
    finally:
        if not published:
            shutil.rmtree(staging, ignore_errors=True)
    return manifest


def read_city_pack(pack_dir: str | Path) -> dict[str, bytes]:
    """Strictly load a complete, verified pack. Any deviation is an error."""
    root = Path(pack_dir)
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise CityPackLoadError(f"City pack has no manifest: {root}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schemaVersion") != PACK_SCHEMA_VERSION:
        raise CityPackLoadError(
            f"Unsupported city pack schemaVersion {manifest.get('schemaVersion')!r}"
        )
    for field in (
        "city",
        "version",
        "sha256",
        "files",
        "totalBytes",
        "source",
        "release",
    ):
        if field not in manifest:
            raise CityPackLoadError(f"City pack manifest field missing: {field}")

    declared = manifest["files"]
    if set(declared) != set(REQUIRED_PACK_FILES):
        raise CityPackLoadError(
            "City pack manifest file set invalid; missing="
            f"{sorted(REQUIRED_PACK_FILES - set(declared))} extra="
            f"{sorted(set(declared) - REQUIRED_PACK_FILES)}"
        )

    on_disk = {
        str(path.relative_to(root)).replace("\\", "/")
        for path in root.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    }
    if on_disk != set(declared):
        raise CityPackLoadError(
            "City pack is partially written or has foreign files: expected "
            f"{sorted(set(declared))}, found {sorted(on_disk)}",
            partial=True,
        )

    files: dict[str, bytes] = {}
    payload = bytearray()
    total_bytes = 0
    for name in sorted(declared):
        data = (root / name).read_bytes()
        entry = declared[name]
        if len(data) != entry["bytes"]:
            raise CityPackLoadError(
                f"{name}: size {len(data)} != manifest {entry['bytes']}",
                partial=True,
            )
        digest = hashlib.sha256(data).hexdigest()
        if digest != entry["sha256"]:
            raise CityPackLoadError(f"{name}: sha256 mismatch")
        encoded_name = name.encode("utf-8")
        payload.extend(struct.pack("<I", len(encoded_name)))
        payload.extend(encoded_name)
        payload.extend(data)
        total_bytes += len(data)
        files[name] = data

    if total_bytes != manifest["totalBytes"]:
        raise CityPackLoadError(
            f"totalBytes {manifest['totalBytes']} != on-disk {total_bytes}"
        )
    if hashlib.sha256(bytes(payload)).hexdigest() != manifest["sha256"]:
        raise CityPackLoadError("City pack payload sha256 mismatch")
    return files


def verify_city_pack(pack_dir: str | Path) -> None:
    read_city_pack(pack_dir)


def _grid_cell_size(extent_m: float) -> float:
    """Adaptive TAZ grid: about 24 cells across the longest city axis."""
    return min(2000.0, max(200.0, round(extent_m / 24.0)))


def build_city_zones(
    places: tuple[CityPlace, ...],
    *,
    origin_lon: float,
    origin_lat: float,
    stops_metric: tuple[tuple[float, float], ...] = (),
) -> tuple[DemandZone, ...]:
    """Grid zones with place-derived attraction proxy for pop/jobs.

    Overture places carry no census counts; population and jobs use the
    projected place-importance mass (work importance as employment, all
    other mapped purposes as a residential proxy). Census calibration of
    this proxy belongs to Этап 6-7.
    """
    points = [
        (
            project_wgs84_point(
                place.location,
                origin_lon=origin_lon,
                origin_lat=origin_lat,
            ),
            place,
        )
        for place in places
    ]
    xs = [point.x for point, _ in points] + [stop[0] for stop in stops_metric]
    ys = [point.y for point, _ in points] + [stop[1] for stop in stops_metric]
    if not xs or not ys:
        return ()
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    if max_x - min_x < 100.0:
        min_x -= 1000.0
        max_x += 1000.0
    if max_y - min_y < 100.0:
        min_y -= 1000.0
        max_y += 1000.0
    cell = _grid_cell_size(max(max_x - min_x, max_y - min_y))
    zones = generate_grid_zones(min_x, min_y, max_x, max_y, cell_size=cell)

    projected_places = tuple(
        replace(
            place,
            location=project_wgs84_point(
                place.location,
                origin_lon=origin_lon,
                origin_lat=origin_lat,
            ),
        )
        for _point, place in points
    )
    enriched = aggregate_place_attractions(zones, projected_places)

    result: list[DemandZone] = []
    for zone in enriched:
        work = zone.attractions.get("work", 0.0)
        other = sum(
            value
            for purpose, value in zone.attractions.items()
            if purpose != "work"
        )
        result.append(
            replace(
                zone,
                population=other,
                jobs=work,
            )
        )
    return tuple(result)


def build_overture_city_pack(
    *,
    city: str,
    version: str,
    source: OvertureSource = OvertureSource(),
    bbox: tuple[float, float, float, float] | None = None,
    zones: tuple[DemandZone, ...] = (),
    demand_config: CityDemandConfig = CityDemandConfig(),
) -> tuple[CityPackManifest, dict[str, bytes]]:
    network = OvertureNetworkProvider(
        source=source,
        bbox=bbox,
        snap_max_distance_m=150.0,
    ).load(
        include_connectors=True,
        include_stops=True,
        include_places=True,
    )
    buildings, water = OvertureUrbanProvider(
        source=source,
        bbox=bbox,
    ).load()

    if not zones:
        zones = build_city_zones(
            network.places,
            origin_lon=network.origin_lon,
            origin_lat=network.origin_lat,
            stops_metric=tuple(
                (stop.location.x, stop.location.y) for stop in network.stops_metric
            ),
        )

    nodes = tuple(
        (
            node_id,
            project_local_point_wgs84(
                Point(node.x, node.y),
                origin_lon=network.origin_lon,
                origin_lat=network.origin_lat,
            ),
        )
        for node_id, node in sorted(network.graph.nodes.items())
    )
    edges = tuple(network.graph.edges[edge_id] for edge_id in sorted(network.graph.edges))
    names = tuple(edge.id for edge in edges)
    streets = {
        "version": 1,
        "crs": "OGC:CRS84",
        "binary": "TKST",
        "nodes": len(nodes),
        "edges": len(edges),
        "originLon": network.origin_lon,
        "originLat": network.origin_lat,
    }

    stops = [
        {
            "id": stop.id,
            "name": stop.name,
            "lon": stop.location.x,
            "lat": stop.location.y,
            "is_station": stop.is_station,
        }
        for stop in network.stops
    ]

    zone_index = {zone.id: index for index, zone in enumerate(zones)}
    demand = build_daily_demand(
        zones,
        trip_rate=demand_config.trip_rate,
        decay=demand_config.decay,
        reference_speed_kph=demand_config.reference_speed_kph,
    )
    purposes = sorted({pair.purpose for pair in demand.pairs})
    purpose_index = {purpose: index for index, purpose in enumerate(purposes)}
    demand_rows = [
        (
            zone_index[pair.origin_zone_id],
            zone_index[pair.destination_zone_id],
            pair.trips_per_day,
            pair.base_time_min,
            purpose_index[pair.purpose],
        )
        for pair in demand.pairs
        if pair.trips_per_day > 0
        and pair.origin_zone_id in zone_index
        and pair.destination_zone_id in zone_index
    ]

    stop_rows = [
        (stop["id"], stop["lon"], stop["lat"], stop["is_station"]) for stop in stops
    ]
    zone_rows = [
        (zone.id, zone.centroid_x, zone.centroid_y, zone.population, zone.jobs)
        for zone in zones
    ]
    zone_attractions = [
        [(purpose, value) for purpose, value in zone.purpose_attractions]
        for zone in zones
    ]

    model_path = Path(__file__).with_name("model.json")
    files = {
        "streets.json": json.dumps(
            streets, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8"),
        "streets.bin": encode_streets_bin(nodes=nodes, edges=edges, names=names),
        "stops.bin": encode_stops_bin(stops=stop_rows),
        "zones.bin": encode_zones_bin(zones=zone_rows, attractions=zone_attractions),
        "demand.bin": encode_demand_bin(
            zone_count=len(zones), pairs=demand_rows, purposes=purposes
        ),
        "buildings.bin": encode_buildings_bin(buildings),
        "water.bin": encode_water_bin(water),
        "model.json": model_path.read_bytes(),
    }
    return pack_city_files(city, version, files, release=source.release)


def build_and_write_overture_city_pack(
    output_dir: str | Path,
    *,
    city: str,
    version: str,
    source: OvertureSource = OvertureSource(),
    bbox: tuple[float, float, float, float] | None = None,
    zones: tuple[DemandZone, ...] = (),
    demand_config: CityDemandConfig = CityDemandConfig(),
) -> CityPackManifest:
    manifest, files = build_overture_city_pack(
        city=city,
        version=version,
        source=source,
        bbox=bbox,
        zones=zones,
        demand_config=demand_config,
    )
    return write_city_pack(
        output_dir,
        city,
        version,
        files,
        release=manifest.release,
    )
