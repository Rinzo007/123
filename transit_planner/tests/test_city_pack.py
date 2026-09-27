import hashlib
import struct

from transit_planner.city_pack import encode_tkbl, pack_city_files


def test_tkbl_header_and_roundtrip_bytes():
    data = encode_tkbl([
        [(39.2, 51.67), (39.21, 51.68)],
        [(39.3, 51.69)],
    ])

    assert data[:4] == b"TKBL"
    version, flags, line_count, point_count = struct.unpack_from("<HHII", data, 4)
    assert version == 1
    assert flags == 0
    assert line_count == 2
    assert point_count == 3

    offsets = struct.unpack_from("<III", data, 16)
    assert offsets == (0, 2, 3)


def test_city_pack_manifest_contains_per_file_checksums():
    manifest, files = pack_city_files(
        "demo",
        "1",
        {"streets.bin": b"TKBL", "model.json": b"{}"},
    )
    assert manifest.city == "demo"
    assert manifest.total == 6
    assert manifest.files["streets.bin"]["bytes"] == 4
    assert manifest.files["model.json"]["bytes"] == 2
    assert manifest.files["model.json"]["sha256"] == hashlib.sha256(b"{}").hexdigest()
    assert set(files) == {"streets.bin", "model.json"}


def test_city_pack_payload_hash_is_stable_for_file_order():
    left, _ = pack_city_files("demo", "1", {"b": b"2", "a": b"1"})
    right, _ = pack_city_files("demo", "1", {"a": b"1", "b": b"2"})
    assert left.sha256 == right.sha256
