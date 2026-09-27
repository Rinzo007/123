from __future__ import annotations

import json
import struct
from typing import Iterable, Sequence

from .geo import Point
from .urban import BuildingFootprint, WaterFeature


STREETS_MAGIC = 0x54534B54  # little-endian bytes: 54 4b 53 54 = "TKST"
BUILDINGS_MAGIC = 0x4C424B54  # little-endian bytes: 54 4b 42 4c = "TKBL"
STOPS_MAGIC = 0x50534B54  # "TKSP"
ZONES_MAGIC = 0x4E5A4B54  # "TKZN"
DEMAND_MAGIC = 0x4D444B54  # "TKDM"
WATER_MAGIC = 0x52574B54  # "TKWR"

VERSION = 1
HEADER_BYTES = 24  # 6 x little-endian uint32


def encode_buildings_bin(buildings: Iterable[BuildingFootprint]) -> bytes:
    polygons: list[tuple[Point, ...]] = []
    for building in buildings:
        polygons.extend(
            polygon
            for polygon in building.polygons
            if len(polygon) >= 3
        )
    if not polygons:
        return struct.pack("<6I", BUILDINGS_MAGIC, VERSION, 0, 0, 0, 0)

    all_points = [point for polygon in polygons for point in polygon]
    origin_lon = round(min(point.x for point in all_points) * 1_000_000)
    origin_lat = round(min(point.y for point in all_points) * 1_000_000)

    payload = bytearray()
    offsets = [0]
    current_x = 0
    current_y = 0

    for polygon in polygons:
        for point in polygon:
            target_x = round(point.x * 1_000_000) - origin_lon
            target_y = round(point.y * 1_000_000) - origin_lat
            dx = target_x - current_x
            dy = target_y - current_y
            if -32767 <= dx <= 32767 and -32767 <= dy <= 32767:
                payload.extend(struct.pack("<hh", dx, dy))
            else:
                payload.extend(struct.pack("<hhii", -32768, -32768, target_x, target_y))
            current_x = target_x
            current_y = target_y
        offsets.append(offsets[-1] + len(polygon))

    header = struct.pack(
        "<6I",
        BUILDINGS_MAGIC,
        VERSION,
        len(polygons),
        len(all_points),
        origin_lon & 0xFFFFFFFF,
        origin_lat & 0xFFFFFFFF,
    )
    offset_bytes = b"".join(struct.pack("<I", value) for value in offsets)
    return bytes(header + offset_bytes + payload)


def _road_class_code(road_type: str) -> int:
    value = road_type.lower().replace("-", "_").strip()
    if value in {"busway", "bus_expressway"}:
        return 5
    return {
        "motorway": 4,
        "trunk": 3,
        "primary": 2,
        "secondary": 1,
        "tertiary": 0,
        "unclassified": 0,
        "residential": 0,
        "living_street": 0,
        "service": 0,
    }.get(value, 0)


def encode_streets_bin(
    *,
    nodes: Sequence[tuple[int, Point]],
    edges: Sequence[object],
    names: Sequence[str],
) -> bytes:
    node_index = {node_id: index for index, (node_id, _point) in enumerate(nodes)}
    vertex_count = len(nodes)
    edge_count = len(edges)

    vertex_buffer = bytearray()
    for _node_id, point in nodes:
        vertex_buffer.extend(
            struct.pack("<ii", round(point.x * 1_000_000), round(point.y * 1_000_000))
        )

    edge_a = bytearray()
    edge_b = bytearray()
    edge_len = bytearray()
    edge_name = bytearray()
    flat_off = bytearray()
    edge_cls = bytearray()
    flat_buffer = bytearray()
    flat_count = 0

    name_index = {name: index for index, name in enumerate(names)}
    for edge in edges:
        edge_a.extend(struct.pack("<I", node_index[edge.from_node]))
        edge_b.extend(struct.pack("<I", node_index[edge.to_node]))
        edge_len.extend(struct.pack("<I", max(0, round(edge.length_m))))
        edge_name.extend(struct.pack("<i", name_index.get(edge.id, -1)))
        flat_off.extend(struct.pack("<I", flat_count))

        geometry = tuple(edge.geometry or ())
        if not geometry:
            start = nodes[node_index[edge.from_node]][1]
            end = nodes[node_index[edge.to_node]][1]
            geometry = (start, end)
        start = geometry[0]
        previous_x = round(start.x * 1_000_000)
        previous_y = round(start.y * 1_000_000)
        for point in geometry[1:]:
            current_x = round(point.x * 1_000_000)
            current_y = round(point.y * 1_000_000)
            flat_buffer.extend(
                struct.pack("<ii", current_x - previous_x, current_y - previous_y)
            )
            previous_x = current_x
            previous_y = current_y
            flat_count += 1

    flat_off.extend(struct.pack("<I", flat_count))
    edge_cls.extend(
        _road_class_code(str(getattr(edge, "road_type", "unknown"))).to_bytes(1, "little")
        for edge in edges
    )

    names_payload = json.dumps(
        {"names": list(names)},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")

    header = struct.pack(
        "<6I",
        STREETS_MAGIC,
        VERSION,
        vertex_count,
        edge_count,
        flat_count,
        len(names_payload),
    )
    body = bytearray()
    body.extend(vertex_buffer)
    body.extend(edge_a)
    body.extend(edge_b)
    body.extend(edge_len)
    body.extend(edge_name)
    body.extend(flat_off)
    body.extend(edge_cls)
    while len(body) % 4:
        body.append(0)
    body.extend(flat_buffer)
    body.extend(names_payload)
    return bytes(header + body)


def encode_stops_bin(
    *,
    stops: Sequence[tuple[str, float, float, bool]],
) -> bytes:
    """Encode (id, lon, lat, is_station) stops as TKSP typed arrays."""
    count = len(stops)
    coords = bytearray()
    flags = bytearray()
    for _id, lon, lat, is_station in stops:
        coords.extend(struct.pack("<ii", round(lon * 1_000_000), round(lat * 1_000_000)))
        flags.append(1 if is_station else 0)
    names_payload = json.dumps(
        {"names": [stop[0] for stop in stops]},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    header = struct.pack(
        "<6I",
        STOPS_MAGIC,
        VERSION,
        count,
        len(coords),
        len(flags),
        len(names_payload),
    )
    return bytes(header + coords + flags + names_payload)


def decode_stops_bin(data: bytes) -> list[tuple[str, float, float, bool]]:
    magic, version, count, coords_bytes, flags_bytes, names_bytes = struct.unpack_from(
        "<6I", data, 0
    )
    if magic != STOPS_MAGIC or version != VERSION:
        raise ValueError("Неверный заголовок stops.bin")
    names = json.loads(data[HEADER_BYTES + coords_bytes + flags_bytes:])[
        "names"
    ]
    stops: list[tuple[str, float, float, bool]] = []
    for index in range(count):
        lon, lat = struct.unpack_from("<ii", data, HEADER_BYTES + index * 8)
        flag = data[HEADER_BYTES + coords_bytes + index]
        stops.append((names[index], lon / 1_000_000.0, lat / 1_000_000.0, bool(flag)))
    return stops


def encode_zones_bin(
    *,
    zones: Sequence[tuple[str, float, float, float, float]],
    attractions: Sequence[Sequence[tuple[str, float]]] = (),
) -> bytes:
    """Encode (id, centroid_x, centroid_y, population, jobs) zones as TKZN."""
    count = len(zones)
    rows = bytearray()
    for _id, centroid_x, centroid_y, population, jobs in zones:
        rows.extend(struct.pack("<dddd", centroid_x, centroid_y, population, jobs))
    payload: dict[str, object] = {
        "names": [zone[0] for zone in zones],
        "attractions": [list(list(pair) for pair in row) for row in attractions],
    }
    names_payload = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    header = struct.pack(
        "<6I",
        ZONES_MAGIC,
        VERSION,
        count,
        len(rows),
        0,
        len(names_payload),
    )
    return bytes(header + rows + names_payload)


def decode_zones_bin(data: bytes) -> list[tuple[str, float, float, float, float]]:
    magic, version, count, rows_bytes, _r, _n = struct.unpack_from("<6I", data, 0)
    if magic != ZONES_MAGIC or version != VERSION:
        raise ValueError("Неверный заголовок zones.bin")
    names = json.loads(data[HEADER_BYTES + rows_bytes:])["names"]
    zones: list[tuple[str, float, float, float, float]] = []
    for index in range(count):
        centroid_x, centroid_y, population, jobs = struct.unpack_from(
            "<dddd", data, HEADER_BYTES + index * 32
        )
        zones.append((names[index], centroid_x, centroid_y, population, jobs))
    return zones


def encode_demand_bin(
    *,
    zone_count: int,
    pairs: Sequence[tuple[int, int, float, float | None, int]],
    purposes: Sequence[str],
) -> bytes:
    """Encode OD rows (o, d, trips, base_time_min, purpose_index) with a purpose table."""
    count = len(pairs)
    rows = bytearray()
    for origin, destination, trips, base_time_min, purpose_index in pairs:
        if not 0 <= origin < zone_count or not 0 <= destination < zone_count:
            raise ValueError("Demand OD index outside the zone table")
        if not 0 <= purpose_index < len(purposes):
            raise ValueError("Demand purpose index outside the purpose table")
        rows.extend(
            struct.pack(
                "<IIIdd",
                origin,
                destination,
                purpose_index,
                trips,
                -1.0 if base_time_min is None else base_time_min,
            )
        )
    names_payload = json.dumps(
        {"purposes": list(purposes)},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    header = struct.pack(
        "<6I",
        DEMAND_MAGIC,
        VERSION,
        count,
        len(rows),
        0,
        len(names_payload),
    )
    return bytes(header + rows + names_payload)


def decode_demand_bin(data: bytes) -> list[tuple[int, int, float, float | None, str]]:
    magic, version, count, rows_bytes, _r, _n = struct.unpack_from("<6I", data, 0)
    if magic != DEMAND_MAGIC or version != VERSION:
        raise ValueError("Неверный заголовок demand.bin")
    purposes = json.loads(data[HEADER_BYTES + rows_bytes:])["purposes"]
    pairs: list[tuple[int, int, float, float | None, str]] = []
    for index in range(count):
        origin, destination, purpose_index, trips, base_time_min = struct.unpack_from(
            "<IIIdd", data, HEADER_BYTES + index * 28
        )
        pairs.append(
            (
                origin,
                destination,
                trips,
                None if base_time_min < 0 else base_time_min,
                purposes[purpose_index],
            )
        )
    return pairs


def encode_water_bin(water: Iterable[WaterFeature]) -> bytes:
    """Encode water polygons with the same TKBL-style delta layout."""
    polygons: list[tuple[Point, ...]] = []
    for feature in water:
        polygons.extend(
            polygon for polygon in feature.polygons if len(polygon) >= 3
        )
    if not polygons:
        return struct.pack("<6I", WATER_MAGIC, VERSION, 0, 0, 0, 0)

    all_points = [point for polygon in polygons for point in polygon]
    origin_lon = round(min(point.x for point in all_points) * 1_000_000)
    origin_lat = round(min(point.y for point in all_points) * 1_000_000)

    payload = bytearray()
    offsets = [0]
    current_x = 0
    current_y = 0
    for polygon in polygons:
        for point in polygon:
            target_x = round(point.x * 1_000_000) - origin_lon
            target_y = round(point.y * 1_000_000) - origin_lat
            dx = target_x - current_x
            dy = target_y - current_y
            if -32767 <= dx <= 32767 and -32767 <= dy <= 32767:
                payload.extend(struct.pack("<hh", dx, dy))
            else:
                payload.extend(struct.pack("<hhii", -32768, -32768, target_x, target_y))
            current_x = target_x
            current_y = target_y
        offsets.append(offsets[-1] + len(polygon))

    header = struct.pack(
        "<6I",
        WATER_MAGIC,
        VERSION,
        len(polygons),
        len(all_points),
        origin_lon & 0xFFFFFFFF,
        origin_lat & 0xFFFFFFFF,
    )
    offset_bytes = b"".join(struct.pack("<I", value) for value in offsets)
    return bytes(header + offset_bytes + payload)
