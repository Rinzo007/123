from __future__ import annotations

import hashlib
import json
import shutil
import struct
import tempfile
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping

from .binary_pack import (
    STREETS_VERSION,
    encode_buildings_bin,
    encode_demand_bin,
    encode_stops_bin,
    encode_streets_bin,
    encode_water_bin,
    encode_zones_bin,
)
from .control_totals import (
    jobs_from_workplace_floor_area,
)
from .demand_points import load_demand_points
from .building_class import (
    BuildingClass,
    BuildingClassification,
    ExternalSignals,
    classify_buildings,
    classification_summary,
)
from .city import DemandZone
from .geo import Point
from .population_raster import (
    PopulationRaster,
    find_population_raster,
    population_by_zone,
    read_population_cells,
)
from .study_area import StudyArea
from .urban import (
    BuildingFootprint,
    effective_floors,
    polygon_area_m2,
)
from .places import CityPlace, aggregate_place_attractions
from .projection import project_local_point_wgs84, project_wgs84_point
from .road import RoadGraph
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
    provenance: dict = field(default_factory=dict)

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
            "provenance": self.provenance,
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
    provenance: dict | None = None,
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
        provenance=dict(provenance or {}),
    )
    return manifest, normalized


def write_city_pack(
    output_dir: str | Path,
    city: str,
    version: str,
    files: Mapping[str, bytes],
    *,
    release: str = "",
    provenance: dict | None = None,
) -> CityPackManifest:
    """Atomically write a pack: stage into a temp dir, verify, then swap.

    A partially written pack is never visible under the target path.
    """
    manifest, normalized = pack_city_files(
        city, version, files, release=release, provenance=provenance
    )
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


def jobs_by_purpose(
    workplace_area_by_purpose: dict[str, dict[str, float]],
    population_total: float,
) -> dict[str, dict[str, float]]:
    """Занятость, разложенная по типам рабочих зданий.

    Киевский эталон делает то же самое: рабочая сторона размечена типами
    (университеты, больницы, аэропорты, музеи), и спецспрос получается сам, из
    занятости, а не добавляется отдельным слоем поверх гравитации.

    Величина везде одна - население: рынок труда закрыт, сумма jobs равна
    сумме residents. Типы лишь задают, как эта сумма делится между зонами.
    """
    cleaned = {
        purpose: {zone: area for zone, area in bucket.items() if area > 0}
        for purpose, bucket in workplace_area_by_purpose.items()
    }
    total_area = sum(sum(bucket.values()) for bucket in cleaned.values())
    if total_area <= 0 or population_total <= 0:
        return {}
    return {
        purpose: {
            zone: population_total * area / total_area for zone, area in bucket.items()
        }
        for purpose, bucket in cleaned.items()
    }


def _nearest_zone(zones: tuple[DemandZone, ...], point: Point) -> DemandZone | None:
    return min(
        zones,
        key=lambda zone: (
            (point.x - zone.centroid_x) ** 2 + (point.y - zone.centroid_y) ** 2
        ),
        default=None,
    )


def _workplace_floor_area_by_zone(
    buildings: tuple[BuildingFootprint, ...],
    classifications: tuple[BuildingClassification, ...],
    zones: tuple[DemandZone, ...],
    *,
    origin_lon: float,
    origin_lat: float,
    by_purpose: bool = False,
) -> dict:
    """Площадь рабочих зданий по зонам - основание для занятости.

    С `by_purpose=True` возвращает `{purpose: {zone_id: площадь}}`. Тип
    здания важнее его размера: именно он определяет, в какой purpose уйдут
    поездки, а без разбивки вся занятость сливается в общую `work`.
    """
    if not classifications or not buildings:
        return {} if by_purpose else {}
    workplace = {
        item.building_id
        for item in classifications
        if item.building_class is BuildingClass.WORKPLACE
    }
    by_id = {item.building_id: item for item in classifications}
    totals: dict = {zone.id: 0.0 for zone in zones} if not by_purpose else {}
    for building in buildings:
        if building.id not in workplace:
            continue
        centroid = _building_centroid(building)
        if centroid is None:
            continue
        point = project_wgs84_point(
            centroid, origin_lon=origin_lon, origin_lat=origin_lat
        )
        nearest = _nearest_zone(zones, point)
        if nearest is None:
            continue
        area = building.area_m2 * effective_floors(building)
        if not by_purpose:
            totals[nearest.id] += area
            continue
        purpose = by_id[building.id].workplace_purpose or "work"
        bucket = totals.setdefault(purpose, {})
        bucket[nearest.id] = bucket.get(nearest.id, 0.0) + area
    if by_purpose:
        return {
            purpose: {zone: area for zone, area in bucket.items() if area > 0}
            for purpose, bucket in totals.items()
        }
    return {zone_id: area for zone_id, area in totals.items() if area > 0}


def _building_centroid(building: BuildingFootprint) -> Point | None:
    """Взвешенный центроид площади полигонов здания."""
    total_area = 0.0
    sum_x = 0.0
    sum_y = 0.0
    for polygon in building.polygons:
        if len(polygon) < 3:
            continue
        area = abs(polygon_area_m2(polygon))
        if area <= 0:
            continue
        xs = [point.x for point in polygon]
        ys = [point.y for point in polygon]
        sum_x += (sum(xs) / len(xs)) * area
        sum_y += (sum(ys) / len(ys)) * area
        total_area += area
    if total_area <= 0:
        return None
    return Point(sum_x / total_area, sum_y / total_area)


def build_city_zones(
    places: tuple[CityPlace, ...],
    *,
    origin_lon: float,
    origin_lat: float,
    stops_metric: tuple[tuple[float, float], ...] = (),
    buildings: tuple[BuildingFootprint, ...] = (),
    population_controls_factory: Callable[[tuple[DemandZone, ...]], dict[str, float]] | None = None,
) -> tuple[DemandZone, ...]:
    """Grid zones with a residential mass from Overture building footprints.

    Population is counted as residential floor area divided by floor area per
    person, using the `num_floors`/`height` that Overture carries on buildings.
    The estimate is knowingly low where Overture building coverage is missing,
    and the direction of the bias is recorded in the pack provenance rather
    than hidden: this is an estimate, not a census.

    Jobs keep the place-importance proxy. A building footprint cannot separate
    an office block from a warehouse, and Overture carries no employment
    count, so jobs have no building-area counterpart.
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
    classifications = classify_buildings(buildings)
    workplace_area = _workplace_floor_area_by_zone(
        buildings, classifications, zones, origin_lon=origin_lon, origin_lat=origin_lat
    )
    workplace_by_purpose = _workplace_floor_area_by_zone(
        buildings, classifications, zones, origin_lon=origin_lon, origin_lat=origin_lat,
        by_purpose=True,
    )

    # Население берётся только из растра контрольных итогов. Оценка «жилая
    # площадь делить на площадь на человека» удалена: она опиралась на
    # FLOOR_AREA_PER_PERSON_M2, то есть на константу без источника, и при
    # отсутствии растра молча выдавала бы неопределённое население.
    population_controls: dict[str, float] = {}
    if population_controls_factory is not None:
        population_controls = population_controls_factory(zones)
    residential = {
        zone_id: people
        for zone_id, people in population_controls.items()
        if people > 0
    }
    if population_controls:
        method = "raster_control_total"
        _control_report = None
    else:
        method = "raster_control_missing"
        _control_report = None

    # Занятость: рабочие здания задают форму, население - величину. Так
    # закрытый рынок труда, как в киевском бандле: sums residents == sums jobs.
    # Если рабочих зданий нет, форму берёт прокси мест.
    place_work = {
        zone.id: zone.attractions.get("work", 0.0) for zone in enriched
    }
    jobs_by_zone, jobs_report = jobs_from_workplace_floor_area(
        {zone.id: workplace_area.get(zone.id, 0.0) for zone in zones},
        sum(residential.values()),
        fallback_shape=place_work,
    )

    result: list[DemandZone] = []
    typed_jobs = jobs_by_purpose(workplace_by_purpose, sum(residential.values()))
    # Разворачиваем один раз: иначе сумма по зоне считалась бы заново для
    # каждой зоны, то есть квадратично по числу purpose.
    by_zone: dict[str, dict[str, float]] = {}
    for purpose, bucket in typed_jobs.items():
        for zone_id, value in bucket.items():
            by_zone.setdefault(zone_id, {})[purpose] = round(value, 3)
    for zone in enriched:
        work = zone.attractions.get("work", 0.0)
        # Население только из растра. Прокси мест (важность POI) как источник
        # населения удалён: это не население, и без растра зона остаётся пустой,
        # а pack помечается как raster_control_missing в provenance.
        population = residential.get(zone.id, 0.0)
        # Типизированная занятость заменяет прокси по тем же ключам purpose:
        # аттракции зоны уходят прямо в слой соответствующего назначения.
        attractions = by_zone.get(zone.id, {})
        jobs = sum(attractions.values()) if attractions else jobs_by_zone.get(zone.id, work)
        result.append(
            replace(
                zone,
                population=population,
                jobs=jobs,
                purpose_attractions=tuple(sorted(attractions.items()))
                or zone.purpose_attractions,
            )
        )
    return tuple(result)


def streets_bin_for_graph(
    graph: RoadGraph,
    *,
    origin_lon: float,
    origin_lat: float,
) -> tuple[bytes, dict]:
    """Compress a RoadGraph into TKST v2 with WGS84 coordinates.

    Returns the payload and stats (nodes, edges, components). Node order
    is the ascending node id; edge order is the ascending edge id.
    """

    class _WgsEdge:
        __slots__ = ("id", "from_node", "to_node", "length_m", "speed_kph",
                     "road_type", "direction", "geometry")

        def __init__(self, edge):
            self.id = edge.id
            self.from_node = edge.from_node
            self.to_node = edge.to_node
            self.length_m = edge.length_m
            self.speed_kph = edge.speed_kph
            self.road_type = edge.road_type
            self.direction = edge.direction
            self.geometry = (
                tuple(
                    project_local_point_wgs84(
                        Point(point.x, point.y),
                        origin_lon=origin_lon,
                        origin_lat=origin_lat,
                    )
                    for point in edge.geometry
                )
                if edge.geometry
                else None
            )

    node_ids = sorted(graph.nodes)
    nodes = tuple(
        (
            node_id,
            project_local_point_wgs84(
                Point(graph.nodes[node_id].x, graph.nodes[node_id].y),
                origin_lon=origin_lon,
                origin_lat=origin_lat,
            ),
        )
        for node_id in node_ids
    )
    components = graph.weakly_connected_components()
    edges = tuple(_WgsEdge(graph.edges[edge_id]) for edge_id in sorted(graph.edges))
    names = tuple(edge.id for edge in edges)
    payload = encode_streets_bin(
        nodes=nodes,
        edges=edges,
        names=names,
        components=[components[node_id] for node_id in node_ids],
    )
    return payload, {
        "nodes": len(nodes),
        "edges": len(edges),
        "components": len(set(components.values())),
    }


PACK_PROVENANCE_SCHEME = "transit-planner/pack/v1"

# Насколько далеко остановка может лежать от уличного графа и всё равно
# считаться привязанной. Значение попадает в provenance, поэтому оно одно.
SNAP_MAX_DISTANCE_M = 150.0


def _generator_version() -> str:
    """Версия пакета для provenance.

    Берётся из метаданных установки, а не из константы в коде: иначе версия
    в манифесте разошлась бы с той, что стоит в pyproject.
    """
    try:
        from importlib.metadata import version

        return version("transit-planner")
    except Exception:  # пакет может быть не установлен, а не в wheel
        return "unknown"


def _distribution(values: list[float]) -> dict[str, float]:
    """Сводка по величине: сумма, среднее, максимум, ненулевых."""
    if not values:
        return {"total": 0.0, "mean": 0.0, "max": 0.0, "nonZero": 0}
    return {
        "total": round(sum(values), 3),
        "mean": round(sum(values) / len(values), 3),
        "max": round(max(values), 3),
        "nonZero": sum(1 for value in values if value > 0),
    }


def _pack_provenance(
    *,
    source: OvertureSource,
    bbox: tuple[float, float, float, float] | None,
    snap_max_distance_m: float,
    zones: tuple[DemandZone, ...],
    demand_rows: list[tuple],
    purposes: list[str],
    network,
    buildings: tuple[BuildingFootprint, ...],
    water: tuple,
    streets_stats: dict,
    population_method: str,
    building_classes: dict | None = None,
    study_area: StudyArea | None = None,
    population_raster_info: dict | None = None,
    demand_points_info: dict | None = None,
) -> dict:
    """Откуда взялись цифры пака: источник, параметры, итоги, оговорки.

    Манифест без этого отвечает на вопрос «что за файл», но не «откуда взялось
    население». Здания Overture и прокси мест дают разные числа, поэтому
    выбранный метод, константы пересчёта и оговорки пишутся явно: оценка не
    census.
    """
    caveat_list = [
        "population is a built-area estimate, not census: "
        + {
            "demand_points": "population and jobs read from an external demand-point set; "
            "the field is taken as given and is not recalibrated against the raster, "
            "buildings or a census",
            "raster_control_total": "population read from the GHS-POP raster "
            "(2030 projection, not a census count), distributed over the zone grid",
            "raster_control_missing": "NO POPULATION: the raster was unavailable, so zones "
            "carry zero population and the pack must not be used for assignment",
        }[population_method],
        "jobs are a closed labour market: the total equals the population, with the "
        "spatial shape taken from workplace-classified building floor area",
    ]
    if bbox is None:
        caveat_list.append("no bbox was given, so the pack covers an unbounded query")

    parameters: dict = {
        "snapMaxDistanceM": snap_max_distance_m,
        "populationMethod": population_method,
    }

    return {
        "scheme": PACK_PROVENANCE_SCHEME,
        "generatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "generator": {"name": "transit-planner", "version": _generator_version()},
        "source": {
            "kind": "overture",
            "release": source.release,
            "bbox": list(bbox) if bbox is not None else None,
        },
        "parameters": parameters,
        "inputs": {
            "roads": len(network.roads),
            "connectors": network.graph_build.connector_count,
            "stops": len(network.stops),
            "places": len(network.places),
            "buildings": len(buildings),
            "degenerateSegments": list(network.graph_build.degenerate_segments),
            "water": len(water),
            "zones": len(zones),
        },
        "summary": {
            "streetNodes": streets_stats["nodes"],
            "streetEdges": streets_stats["edges"],
            "streetComponents": streets_stats["components"],
            "population": _distribution([zone.population for zone in zones]),
            "jobs": _distribution([zone.jobs for zone in zones]),
            "odPairs": len(demand_rows),
            "tripsPerDay": round(sum(row[2] for row in demand_rows), 3),
            "purposes": {
                purpose: sum(1 for row in demand_rows if purposes[row[4]] == purpose)
                for purpose in purposes
            },
        },
        "caveats": caveat_list,
        "buildingClassification": building_classes or {},
        "studyArea": study_area.to_provenance() if study_area is not None else None,
        "demandPoints": demand_points_info or None,
        "populationRaster": population_raster_info or None,
    }


def _clip_to_study_area(
    buildings: tuple[BuildingFootprint, ...],
    water: tuple,
    study_area: StudyArea | None,
) -> tuple[tuple[BuildingFootprint, ...], tuple]:
    """Отсекает здания и воду по полигону границы.

    Сеть фильтруется в провайдере, до сборки графа; здесь остаётся то, что
    приходит отдельным каналом. Здание режется по своему центроиду.
    """
    if study_area is None:
        return buildings, water
    kept = tuple(
        building
        for building in buildings
        if (centroid := _building_centroid(building)) is not None
        and study_area.contains(centroid.x, centroid.y)
    )
    kept_water = tuple(
        feature
        for feature in water
        if any(
            study_area.contains(point.x, point.y)
            for polygon in feature.polygons
            for point in polygon
        )
    )
    return kept, kept_water


def build_overture_city_pack(
    *,
    city: str,
    version: str,
    source: OvertureSource = OvertureSource(),
    bbox: tuple[float, float, float, float] | None = None,
    zones: tuple[DemandZone, ...] = (),
    study_area: StudyArea | None = None,
    demand_points_path: str | Path | None = None,
) -> tuple[CityPackManifest, dict[str, bytes]]:
    """Собирает пак города.

    `study_area` задаёт область исследования полигоном: Overture выбирается по
    bbox, описанному границей, а затем дороги, здания и POI отсекаются по
    самому полигону. Без этого в пак попадает всё, что лежит в
    описывающем прямоугольнике, а у Тамбова это 54.5% лишней площади.

    `demand_points_path` заменяет сетку TAZ внешним набором точек спроса: у
    эталонного набора это десятки тысяч зон с населением и занятостью в
    каждой, тогда как сетка даёт сотни. Когда точки заданы, население берётся
    из них, а не из растра или площади зданий, - собственная величина
    источника точнее любой оценки.
    """
    if study_area is not None:
        bbox = study_area.bbox
    network = OvertureNetworkProvider(
        source=source,
        bbox=bbox,
        snap_max_distance_m=SNAP_MAX_DISTANCE_M,
    ).load(
        include_connectors=True,
        include_stops=True,
        include_places=True,
        # Отсечение по полигону границы, а не по её bbox: у Тамбова
        # описывающий прямоугольник вдвое больше города.
        area_filter=study_area.contains_point if study_area is not None else None,
    )
    buildings, water = OvertureUrbanProvider(
        source=source,
        bbox=bbox,
    ).load()
    buildings, water = _clip_to_study_area(buildings, water, study_area)

    points_info: dict = {}
    if demand_points_path is not None and not zones:
        zones, points_source = load_demand_points(
            demand_points_path,
            origin_lon=network.origin_lon,
            origin_lat=network.origin_lat,
            inside=study_area.contains if study_area is not None else None,
        )
        points_info = points_source.to_provenance()

    population_raster_info: dict = {}
    raster_read = None
    # Растр нужен только когда зоны строятся сеткой: у внешних точек своя
    # величина населения, и подмена её оценкой была бы ухудшением.
    if bbox is not None and not points_info:
        raster_path = find_population_raster(bbox=bbox)
        if raster_path is not None:
            raster = PopulationRaster(path=raster_path)
            raster_read = read_population_cells(bbox, raster)
            population_raster_info = {
                **raster.fingerprint(),
                **raster_read.to_provenance(),
            }

    if not zones:
        # Контрольные итоги приходят по тем же зонам, что и оценка снизу
        # вверх, поэтому сетка строится один раз: фабрика получает готовые
        # зоны и раскладывает по ним население растра.
        def _controls(built: tuple[DemandZone, ...]) -> dict[str, float]:
            if raster_read is None or not raster_read.cells:
                return {}
            return population_by_zone(
                raster_read,
                built,
                origin_lon=network.origin_lon,
                origin_lat=network.origin_lat,
            )

        zones = build_city_zones(
            network.places,
            origin_lon=network.origin_lon,
            origin_lat=network.origin_lat,
            stops_metric=tuple(
                (stop.location.x, stop.location.y) for stop in network.stops_metric
            ),
            buildings=buildings,
            population_controls_factory=_controls,
        )

    streets_payload, streets_stats = streets_bin_for_graph(
        network.graph,
        origin_lon=network.origin_lon,
        origin_lat=network.origin_lat,
    )
    streets = {
        "version": STREETS_VERSION,
        "crs": "OGC:CRS84",
        "binary": "TKST",
        "nodes": streets_stats["nodes"],
        "edges": streets_stats["edges"],
        "components": streets_stats["components"],
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
    if demand_points_info:
        population_method = "demand_points"
    elif population_raster_info:
        population_method = "raster_control_total"
    else:
        population_method = "raster_control_missing"
    files = {
        "streets.json": json.dumps(
            streets, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8"),
        "streets.bin": streets_payload,
        "stops.bin": encode_stops_bin(stops=stop_rows),        "zones.bin": encode_zones_bin(zones=zone_rows, attractions=zone_attractions),
        "demand.bin": encode_demand_bin(
            zone_count=len(zones), pairs=demand_rows, purposes=purposes
        ),
        "buildings.bin": encode_buildings_bin(buildings),
        "water.bin": encode_water_bin(water),
        "model.json": model_path.read_bytes(),
    }
    return pack_city_files(
        city,
        version,
        files,
        release=source.release,
        provenance=_pack_provenance(
            source=source,
            bbox=bbox,
            snap_max_distance_m=SNAP_MAX_DISTANCE_M,
            zones=zones,
            demand_rows=demand_rows,
            purposes=purposes,
            network=network,
            buildings=buildings,
            water=water,
            streets_stats=streets_stats,
            population_method=population_method,
            building_classes=classification_summary(
                classify_buildings(buildings), signals=ExternalSignals()
            ) if buildings else {},
            study_area=study_area,
            population_raster_info=population_raster_info,
            demand_points_info=points_info,
        ),
    )


def build_and_write_overture_city_pack(
    output_dir: str | Path,
    *,
    city: str,
    version: str,
    source: OvertureSource = OvertureSource(),
    bbox: tuple[float, float, float, float] | None = None,
    zones: tuple[DemandZone, ...] = (),
) -> CityPackManifest:
    manifest, files = build_overture_city_pack(
        city=city,
        version=version,
        source=source,
        bbox=bbox,
        zones=zones,
    )
    return write_city_pack(
        output_dir,
        city,
        version,
        files,
        release=manifest.release,
        provenance=manifest.provenance,
    )
