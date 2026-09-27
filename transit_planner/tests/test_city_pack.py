import hashlib
import json
import struct

import pytest

from transit_planner.binary_pack import (
    decode_demand_bin,
    decode_stops_bin,
    decode_zones_bin,
    encode_demand_bin,
    encode_stops_bin,
    encode_zones_bin,
)
from transit_planner.city_pack import (
    REQUIRED_PACK_FILES,
    CityPackError,
    CityPackLoadError,
    build_city_zones,
    encode_tkbl,
    pack_city_files,
    read_city_pack,
    verify_city_pack,
    write_city_pack,
)
from transit_planner.geo import Point
from transit_planner.places import CityPlace


def test_build_city_zones_grids_places_into_taz():
    places = tuple(
        CityPlace(
            id=f"p{index}",
            name=f"Place {index}",
            location=Point(39.0 + index * 0.01, 51.0),
            basic_category="office" if index else "school",
            importance=10.0,
        )
        for index in range(4)
    )
    zones = build_city_zones(places, origin_lon=39.0, origin_lat=51.0)
    assert zones
    assert all(zone.id.startswith("z") for zone in zones)
    total_population = sum(zone.population for zone in zones)
    total_jobs = sum(zone.jobs for zone in zones)
    assert total_jobs > 0.0
    assert total_population > 0.0
    work_zones = [zone for zone in zones if zone.jobs > 0.0]
    assert work_zones, "office places must create employment"
    assert all(
        (zone.centroid_x - min(zone.centroid_x for zone in zones)) >= 0
        for zone in zones
    )


def minimal_pack_files() -> dict[str, bytes]:
    return {name: name.encode("utf-8") for name in sorted(REQUIRED_PACK_FILES)}


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
        minimal_pack_files(),
        release="2026-09-23.1",
    )
    assert manifest.city == "demo"
    assert manifest.release == "2026-09-23.1"
    assert manifest.schema_version == 1
    assert manifest.total_bytes == sum(
        len(name.encode("utf-8")) for name in REQUIRED_PACK_FILES
    )
    assert manifest.files["model.json"]["bytes"] == len("model.json")
    assert manifest.files["model.json"]["sha256"] == hashlib.sha256(
        b"model.json"
    ).hexdigest()
    assert set(files) == set(REQUIRED_PACK_FILES)


def test_city_pack_manifest_dict_matches_stage2_spec():
    manifest, _ = pack_city_files("demo", "1", minimal_pack_files(), release="r")
    payload = manifest.to_dict()
    assert set(payload) == {
        "city",
        "version",
        "sha256",
        "files",
        "totalBytes",
        "schemaVersion",
        "source",
        "release",
    }
    assert payload["source"] == "overture"
    assert payload["schemaVersion"] == 1


def test_city_pack_payload_hash_is_stable_for_file_order():
    files = minimal_pack_files()
    left, _ = pack_city_files("demo", "1", files)
    right, _ = pack_city_files("demo", "1", dict(reversed(list(files.items()))))
    assert left.sha256 == right.sha256


def test_pack_rejects_missing_and_foreign_files():
    files = minimal_pack_files()
    del files["water.bin"]
    with pytest.raises(CityPackError, match="missing required"):
        pack_city_files("demo", "1", files)
    with pytest.raises(CityPackError, match="outside the v1 manifest"):
        pack_city_files("demo", "1", {**minimal_pack_files(), "extra.bin": b"x"})


def test_write_and_read_city_pack_roundtrip(tmp_path):
    files = minimal_pack_files()
    manifest = write_city_pack(
        tmp_path / "demo", "demo", "1", files, release="2026-09-23.1"
    )
    loaded = read_city_pack(tmp_path / "demo")
    assert loaded == files

    stored = json.loads((tmp_path / "demo" / "manifest.json").read_text("utf-8"))
    assert stored["release"] == "2026-09-23.1"
    assert stored["totalBytes"] == manifest.total_bytes
    verify_city_pack(tmp_path / "demo")


def test_read_city_pack_rejects_partial_pack(tmp_path):
    write_city_pack(tmp_path / "demo", "demo", "1", minimal_pack_files(), release="r")
    (tmp_path / "demo" / "water.bin").unlink()
    with pytest.raises(CityPackLoadError) as error:
        read_city_pack(tmp_path / "demo")
    assert error.value.partial

    (tmp_path / "demo" / "water.bin").write_bytes(b"water.bin")
    (tmp_path / "demo" / "notes.txt").write_text("foreign file")
    with pytest.raises(CityPackLoadError) as error:
        read_city_pack(tmp_path / "demo")
    assert error.value.partial


def test_read_city_pack_rejects_corrupted_payload(tmp_path):
    write_city_pack(tmp_path / "demo", "demo", "1", minimal_pack_files(), release="r")
    (tmp_path / "demo" / "model.json").write_bytes(b"X" + b"model.json"[1:])
    with pytest.raises(CityPackLoadError) as error:
        read_city_pack(tmp_path / "demo")
    assert not error.value.partial


def test_read_city_pack_rejects_unsupported_schema(tmp_path):
    write_city_pack(tmp_path / "demo", "demo", "1", minimal_pack_files(), release="r")
    path = tmp_path / "demo" / "manifest.json"
    stored = json.loads(path.read_text("utf-8"))
    stored["schemaVersion"] = 2
    path.write_text(json.dumps(stored), "utf-8")
    with pytest.raises(CityPackError, match="schemaVersion"):
        read_city_pack(tmp_path / "demo")


def test_write_city_pack_replaces_previous_pack_atomically(tmp_path):
    first = {name: b"a" for name in REQUIRED_PACK_FILES}
    write_city_pack(tmp_path / "demo", "demo", "1", first, release="r")
    second = {name: b"bb" for name in REQUIRED_PACK_FILES}
    write_city_pack(tmp_path / "demo", "demo", "1", second, release="r2")
    assert read_city_pack(tmp_path / "demo") == second
    assert not list(tmp_path.glob(".demo.staging-*"))


def test_stops_zones_demand_bin_roundtrip():
    stops = [("s1", 39.2, 51.67, False), ("s2", 39.21, 51.68, True)]
    assert decode_stops_bin(encode_stops_bin(stops=stops)) == stops

    zones = [("z1", 0.0, 0.0, 500.0, 250.0), ("z2", 1000.0, 0.0, 300.0, 0.0)]
    decoded_zones = decode_zones_bin(
        encode_zones_bin(zones=zones, attractions=[[("work", 250.0)], []])
    )
    assert decoded_zones == zones

    pairs = [(0, 1, 120.5, 12.5, 1), (1, 0, 80.0, None, 0)]
    decoded = decode_demand_bin(
        encode_demand_bin(zone_count=2, pairs=pairs, purposes=["all", "work"])
    )
    assert decoded == [(0, 1, 120.5, 12.5, "work"), (1, 0, 80.0, None, "all")]


def test_demand_bin_rejects_out_of_range_indices():
    with pytest.raises(ValueError, match="zone table"):
        encode_demand_bin(zone_count=2, pairs=[(0, 5, 1.0, None, 0)], purposes=["all"])
    with pytest.raises(ValueError, match="purpose table"):
        encode_demand_bin(zone_count=2, pairs=[(0, 1, 1.0, None, 3)], purposes=["all"])
