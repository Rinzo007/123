from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .overture_network import OvertureNetworkProvider
from .overture import OvertureSource


TKBL_MAGIC = b"TKBL"
TKBL_VERSION = 1
TKBL_HEADER_BYTES = 16


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
    total: int

    def to_dict(self) -> dict:
        return {
            "city": self.city,
            "version": self.version,
            "sha256": self.sha256,
            "files": self.files,
            "total": self.total,
        }


def encode_tkbl(lines: list[list[tuple[float, float]]]) -> bytes:
    point_count = sum(len(line) for line in lines)
    offsets_bytes = (len(lines) + 1) * 4
    total = TKBL_HEADER_BYTES + offsets_bytes + point_count * 8
    output = bytearray(total)
    output[0:4] = TKBL_MAGIC
    struct.pack_into("<HHII", output, 4, TKBL_VERSION, 0, len(lines), point_count)

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
            struct.pack_into("<ii", output, offset, round(lon * 1_000_000), round(lat * 1_000_000))
            offset += 8
    return bytes(output)


def pack_city_files(
    city: str,
    version: str,
    files: Mapping[str, bytes],
) -> tuple[CityPackManifest, dict[str, bytes]]:
    normalized = {
        name: bytes(data)
        for name, data in sorted(files.items())
        if name and not name.endswith("/")
    }
    if not normalized:
        raise ValueError("City pack must contain at least one file")

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
        total=sum(len(data) for data in normalized.values()),
    )
    return manifest, normalized


def write_city_pack(
    output_dir: str | Path,
    city: str,
    version: str,
    files: Mapping[str, bytes],
) -> CityPackManifest:
    manifest, normalized = pack_city_files(city, version, files)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    for name, data in normalized.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (destination / "manifest.json").write_text(
        json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def build_overture_city_pack(
    *,
    city: str,
    version: str,
    source: OvertureSource = OvertureSource(),
    bbox: tuple[float, float, float, float] | None = None,
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

    street_lines = [
        [
            (float(point.x), float(point.y))
            for point in road.geometry.points
        ]
        for road in network.roads
    ]
    streets = {
        "version": 1,
        "crs": "OGC:CRS84",
        "nodes": [
            {"id": node.id, "x": node.x, "y": node.y}
            for node in network.graph.nodes.values()
        ],
        "edges": [
            {
                "id": edge.id,
                "from": edge.from_node,
                "to": edge.to_node,
                "length_m": edge.length_m,
                "speed_kph": edge.speed_kph,
                "road_type": edge.road_type,
            }
            for edge in network.graph.edges.values()
        ],
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
    places = [
        {
            "id": place.id,
            "name": place.name,
            "lon": place.location.x,
            "lat": place.location.y,
            "basic_category": place.basic_category,
            "taxonomy_primary": place.taxonomy_primary,
            "importance": place.importance,
        }
        for place in network.places
    ]

    model_path = Path(__file__).with_name("model.json")
    files = {
        "streets.json": json.dumps(streets, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        "streets.bin": encode_tkbl(street_lines),
        "stops.json": json.dumps(stops, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        "places.json": json.dumps(places, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        "model.json": model_path.read_bytes(),
    }
    return pack_city_files(city, version, files)


def build_and_write_overture_city_pack(
    output_dir: str | Path,
    *,
    city: str,
    version: str,
    source: OvertureSource = OvertureSource(),
    bbox: tuple[float, float, float, float] | None = None,
) -> CityPackManifest:
    _manifest, files = build_overture_city_pack(
        city=city,
        version=version,
        source=source,
        bbox=bbox,
    )
    return write_city_pack(output_dir, city, version, files)
