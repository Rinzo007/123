from __future__ import annotations

import json
import struct
from typing import Iterable, Sequence

from .geo import Point
from .urban import BuildingFootprint


STREETS_MAGIC = 0x54534B54  # little-endian bytes: 54 4b 53 54 = "TKST"
BUILDINGS_MAGIC = 0x4C424B54  # little-endian bytes: 54 4b 42 4c = "TKBL"
VERSION = 1


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
