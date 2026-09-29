from __future__ import annotations

import json
import tempfile
import time

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from math import asin, cos, radians, sin, sqrt
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .data import (
    ConnectorRecord,
    ConnectorRef,
    ProhibitedTransition,
    ProhibitedTransitionSequenceEntry,
    RoadRecord,
)
from .geo import LineString, Point
from .network import Stop

# Релиз Overture, в котором parquet отдаёт geometry нативным типом GEOMETRY
# ('OGC:CRS84'), а не WKB. Поэтому в SQL геометрия читается напрямую через
# ST_AsGeoJSON(geometry): ST_GeomFromWKB(geometry) падает с BinderException
# «No function matches … st_geomfromwkb(GEOMETRY)», а CAST(geometry AS BLOB)
# не поддерживается. Проверено на всех доступных релизах (2026-08-19.0,
# 2026-09-23.0, 2026-09-23.1) — во всех тип GEOMETRY.
DEFAULT_RELEASE = "2026-09-23.1"
DEFAULT_S3_ROOT = "s3://overturemaps-us-west-2/release"
_DUCKDB_EXTENSION_LOCK = Lock()


@dataclass(frozen=True, slots=True)
class OvertureSource:
    release: str = DEFAULT_RELEASE
    storage_root: str = DEFAULT_S3_ROOT
    transportation_glob: str | None = None
    connector_glob: str | None = None
    infrastructure_glob: str | None = None
    places_glob: str | None = None
    buildings_glob: str | None = None
    water_glob: str | None = None

    def transportation_segments(self) -> str:
        if self.transportation_glob:
            return self.transportation_glob
        return (
            f"{self.storage_root}/{self.release}/"
            "theme=transportation/type=segment/*"
        )

    def transportation_connectors(self) -> str:
        if self.connector_glob:
            return self.connector_glob
        return (
            f"{self.storage_root}/{self.release}/"
            "theme=transportation/type=connector/*"
        )

    def places(self) -> str:
        if self.places_glob:
            return self.places_glob
        return (
            f"{self.storage_root}/{self.release}/"
            "theme=places/type=place/*"
        )

    def infrastructure(self) -> str:
        if self.infrastructure_glob:
            return self.infrastructure_glob
        return (
            f"{self.storage_root}/{self.release}/"
            "theme=base/type=infrastructure/*"
        )


    def buildings(self) -> str:
        if self.buildings_glob:
            return self.buildings_glob
        return (
            f"{self.storage_root}/{self.release}/"
            "theme=buildings/type=building/*"
        )

    def water(self) -> str:
        if self.water_glob:
            return self.water_glob
        return (
            f"{self.storage_root}/{self.release}/"
            "theme=base/type=water/*"
        )


class OverturePlacesProvider:
    """Read Overture Places and expose taxonomy/basic_category for trip demand."""

    def __init__(
        self,
        *,
        source: OvertureSource = OvertureSource(),
        bbox: tuple[float, float, float, float] | None = None,
        categories: tuple[str, ...] | None = None,
    ) -> None:
        self.source = source
        self.bbox = bbox
        self.categories = tuple(categories or ())

    def load_places(self):
        from .places import CityPlace

        rows = _query_duckdb(self._sql())
        places: list[CityPlace] = []
        for place_id, geojson, name, basic_category, taxonomy_primary, taxonomy_hierarchy, confidence in rows:
            if not geojson:
                continue
            geometry = json.loads(geojson)
            coordinates = geometry.get("coordinates") or []
            if len(coordinates) < 2:
                continue
            places.append(
                CityPlace(
                    id=f"overture:{place_id}",
                    name=str(name or place_id),
                    location=Point(float(coordinates[0]), float(coordinates[1])),
                    basic_category=None if basic_category is None else str(basic_category),
                    taxonomy_primary=None if taxonomy_primary is None else str(taxonomy_primary),
                    taxonomy_hierarchy=tuple(str(item) for item in (taxonomy_hierarchy or ())),
                    importance=max(0.0, float(confidence or 1.0)),
                )
            )
        return tuple(places)

    def _sql(self) -> str:
        bbox_filter = _bbox_sql(self.bbox)
        category_filter = ""
        if self.categories:
            values = ", ".join(
                "'" + value.replace("'", "''") + "'"
                for value in self.categories
            )
            category_filter = f"""
              AND (
                basic_category IN ({values})
                OR taxonomy.primary IN ({values})
              )
            """
        return f"""
            SELECT
                id,
                ST_AsGeoJSON(geometry) AS geojson,
                names.primary AS name,
                basic_category,
                taxonomy.primary AS taxonomy_primary,
                taxonomy.hierarchy AS taxonomy_hierarchy,
                confidence
            FROM read_parquet({_parquet_source(self.source.places(), release=self.source.release, overture_type='place', bbox=self.bbox)})
            WHERE TRUE
              {category_filter}
              {bbox_filter}
        """


class OvertureUrbanProvider:
    """Read Overture buildings and water for urban spatial context."""

    def __init__(
        self,
        *,
        source: OvertureSource = OvertureSource(),
        bbox: tuple[float, float, float, float] | None = None,
    ) -> None:
        self.source = source
        self.bbox = bbox

    def load_buildings(self):
        from .urban import BuildingFootprint, parse_polygon_geometry, polygon_area_m2

        rows = _query_duckdb(self._buildings_sql())
        buildings = []
        for building_id, geojson, subtype, building_class, is_underground in rows:
            if not geojson or bool(is_underground):
                continue
            polygons = parse_polygon_geometry(json.loads(geojson))
            if not polygons:
                continue
            area_m2 = sum(polygon_area_m2(polygon) for polygon in polygons)
            if area_m2 <= 0:
                continue
            buildings.append(
                BuildingFootprint(
                    id=f"overture:building:{building_id}",
                    polygons=polygons,
                    area_m2=area_m2,
                    subtype=None if subtype is None else str(subtype),
                    building_class=None if building_class is None else str(building_class),
                    is_underground=bool(is_underground),
                )
            )
        return tuple(buildings)

    def load_water(self):
        from .urban import WaterFeature, parse_polygon_geometry

        rows = _query_duckdb(self._water_sql())
        water = []
        for feature_id, geojson, water_class, subtype in rows:
            if not geojson or str(subtype or "") == "physical":
                continue
            polygons = parse_polygon_geometry(json.loads(geojson))
            if not polygons:
                continue
            water.append(
                WaterFeature(
                    id=f"overture:water:{feature_id}",
                    polygons=polygons,
                    water_class=None if water_class is None else str(water_class),
                    subtype=None if subtype is None else str(subtype),
                )
            )
        return tuple(water)

    def load(self):
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="overture-urban") as pool:
            buildings_future = pool.submit(self.load_buildings)
            water_future = pool.submit(self.load_water)
            return buildings_future.result(), water_future.result()

    def _buildings_sql(self) -> str:
        return f"""
            SELECT
                id,
                ST_AsGeoJSON(geometry) AS geojson,
                subtype,
                class,
                is_underground
            FROM read_parquet({_parquet_source(self.source.buildings(), release=self.source.release, overture_type='building', bbox=self.bbox)})
            WHERE TRUE
              {_bbox_sql(self.bbox)}
        """

    def _water_sql(self) -> str:
        return f"""
            SELECT
                id,
                ST_AsGeoJSON(geometry) AS geojson,
                class,
                subtype
            FROM read_parquet({_parquet_source(self.source.water(), release=self.source.release, overture_type='water', bbox=self.bbox)})
            WHERE TRUE
              {_bbox_sql(self.bbox)}
        """


class OvertureTransportationProvider:
    """Read Overture transportation segments including native connector refs."""

    def __init__(
        self,
        *,
        source: OvertureSource = OvertureSource(),
        bbox: tuple[float, float, float, float] | None = None,
        road_classes: tuple[str, ...] = (
            "motorway",
            "trunk",
            "primary",
            "secondary",
            "tertiary",
            "unclassified",
            "residential",
            "living_street",
            "service",
        ),
    ) -> None:
        self.source = source
        self.bbox = bbox
        self.road_classes = road_classes

    def load_roads(self) -> tuple[RoadRecord, ...]:
        rows = _query_duckdb(self._sql())
        roads: list[RoadRecord] = []
        incomplete_segments: list[tuple[str, int]] = []

        for (
            road_id,
            geojson,
            segment_class,
            subclass,
            oneway,
            connector_refs,
            access_restrictions,
            speed_limits,
            prohibited_transitions,
        ) in rows:
            if not geojson:
                continue

            geometry = json.loads(geojson)
            coordinates = geometry.get("coordinates") or []
            if len(coordinates) < 2:
                continue

            points = tuple(
                Point(float(x), float(y))
                for x, y, *_ in coordinates
            )
            refs = _parse_connector_refs(connector_refs)
            if len(refs) < 2:
                incomplete_segments.append((road_id, len(refs)))
                continue
            source_id = f"overture:{road_id}"
            forward_allowed, backward_allowed = _access_directions(access_restrictions)
            restrictions = _parse_prohibited_transitions(
                prohibited_transitions,
                source_segment_id=source_id,
            )
            road_type = str(segment_class or subclass or "unknown")
            class_speed = _class_speed(road_type)

            roads.append(
                RoadRecord(
                    id=source_id,
                    geometry=LineString(points),
                    speed_kph=_effective_speed_kph(speed_limits, class_speed),
                    road_type=road_type,
                    oneway=bool(oneway) or (forward_allowed and not backward_allowed),
                    connectors=refs,
                    length_m=_haversine_linestring_m(points),
                    forward_allowed=forward_allowed,
                    backward_allowed=backward_allowed,
                    prohibited_transitions=restrictions,
                )
            )

        if incomplete_segments:
            preview = ", ".join(
                f"{segment_id} ({ref_count} ref(s))"
                for segment_id, ref_count in incomplete_segments[:10]
            )
            suffix = (
                f" and {len(incomplete_segments) - 10} more"
                if len(incomplete_segments) > 10
                else ""
            )
            raise ValueError(
                "Overture segments without full connector topology: "
                f"{preview}{suffix}; routing graph requires two or more refs per segment"
            )

        return tuple(roads)

    def load_graph(self):
        from .road_builder import build_topological_road_graph

        roads = self.load_roads()
        # Позиции коннекторов авторитетны; без них граф не собирается, потому
        # что интерполяция по двум сегментам одного коннектора расходится.
        connectors = {
            record.id: record.location
            for record in OvertureConnectorProvider(
                source=self.source, bbox=self.bbox
            ).load_connectors()
        }
        return build_topological_road_graph(roads, connector_locations=connectors)

    def _sql(self) -> str:
        classes = ", ".join(
            "'" + value.replace("'", "''") + "'"
            for value in self.road_classes
        )
        bbox_filter = _bbox_sql(self.bbox)

        return f"""
            SELECT
                id,
                ST_AsGeoJSON(geometry) AS geojson,
                class,
                subclass,
                FALSE AS oneway,
                connectors,
                access_restrictions,
                speed_limits,
                prohibited_transitions
            FROM read_parquet({_parquet_source(self.source.transportation_segments(), release=self.source.release, overture_type='segment', bbox=self.bbox)})
            WHERE subtype = 'road'
              AND (
                class IN ({classes})
                OR subclass IN ({classes})
              )
              {bbox_filter}
        """


class OvertureConnectorProvider:
    """Read Overture transportation connector points."""

    def __init__(
        self,
        *,
        source: OvertureSource = OvertureSource(),
        bbox: tuple[float, float, float, float] | None = None,
    ) -> None:
        self.source = source
        self.bbox = bbox

    def load_connectors(self) -> tuple[ConnectorRecord, ...]:
        rows = _query_duckdb(self._sql())
        connectors: list[ConnectorRecord] = []

        for connector_id, geojson in rows:
            if not geojson:
                continue
            geometry = json.loads(geojson)
            if not isinstance(geometry, dict):
                continue
            lon, lat = _first_position(geometry)
            if lon is None or lat is None:
                continue
            connectors.append(ConnectorRecord(id=str(connector_id), location=Point(lon, lat)))

        return tuple(connectors)

    def _sql(self) -> str:
        return f"""
            SELECT
                id,
                ST_AsGeoJSON(geometry) AS geojson
            FROM read_parquet({_parquet_source(self.source.transportation_connectors(), release=self.source.release, overture_type='connector', bbox=self.bbox)})
            WHERE TRUE
              {_bbox_sql(_dilated(self.bbox))}
        """


class OvertureTransitProvider:
    """Read Overture base-theme transit infrastructure."""

    TRANSIT_CLASSES = (
        "bus_stop",
        "bus_station",
        "platform",
        "stop_position",
        "railway_station",
        "railway_halt",
        "ferry_terminal",
        "subway_station",
    )

    def __init__(
        self,
        *,
        source: OvertureSource = OvertureSource(),
        bbox: tuple[float, float, float, float] | None = None,
    ) -> None:
        self.source = source
        self.bbox = bbox

    def load_stops(self) -> tuple[Stop, ...]:
        rows = _query_duckdb(self._sql())
        stops: dict[str, Stop] = {}

        for stop_id, geojson, name, stop_class in rows:
            if not geojson:
                continue
            geometry = json.loads(geojson)
            if not isinstance(geometry, dict):
                continue

            lon, lat = _first_position(geometry)
            if lon is None or lat is None:
                continue
            stops[f"overture:{stop_id}"] = Stop(
                id=f"overture:{stop_id}",
                name=str(name or stop_class or stop_id),
                location=Point(lon, lat),
                is_station=stop_class in {
                    "bus_station",
                    "railway_station",
                    "railway_halt",
                    "subway_station",
                    "ferry_terminal",
                },
            )

        return tuple(stops.values())

    def _sql(self) -> str:
        classes = ", ".join(
            "'" + value.replace("'", "''") + "'"
            for value in self.TRANSIT_CLASSES
        )
        bbox_filter = _bbox_sql(self.bbox)

        return f"""
            SELECT
                id,
                ST_AsGeoJSON(geometry) AS geojson,
                names.primary AS name,
                class
            FROM read_parquet({_parquet_source(self.source.infrastructure(), release=self.source.release, overture_type='infrastructure', bbox=self.bbox)})
            WHERE subtype = 'transit'
              AND class IN ({classes})
              {bbox_filter}
        """


def _first_position(geometry: dict) -> tuple[float | None, float | None]:
    """Первая координата GeoJSON-геометрии как (lon, lat).

    Остановки приходят как Point, но платформы и stop_position в теме
    infrastructure бывают линиями: у LineString координаты — список точек, и
    наивное `coordinates[:2]` даёт вложенный список вместо числа (TypeError на
    float()). Берём первую вершину: позиция на платформе идентифицирует её не
    хуже, чем точка, а отбрасывать такие строки молча нельзя — тогда город
    теряет остановки целиком.
    """
    coordinates = geometry.get("coordinates")
    position = coordinates
    # Спускаемся по вложенным спискам до пары чисел.
    while isinstance(position, list) and position and isinstance(position[0], list):
        position = position[0]
    if not isinstance(position, list) or len(position) < 2:
        return None, None
    try:
        return float(position[0]), float(position[1])
    except (TypeError, ValueError):
        return None, None


def _query_duckdb(sql: str):
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError(
            "Overture providers require duckdb"
        ) from exc

    connection = duckdb.connect()
    try:
        with _DUCKDB_EXTENSION_LOCK:
            connection.execute("INSTALL httpfs")
            connection.execute("LOAD httpfs")
            connection.execute("INSTALL spatial")
            connection.execute("LOAD spatial")
        connection.execute("SET s3_region='us-west-2'")
        # Явный S3-секрет обязателен: без него httpfs ходит в бакет анонимно и
        # S3 периодически обрывает соединение с "SSL connect error" на середине
        # чтения parquet. С секретом идёт штатный S3-протокол без редиректов.
        connection.execute(
            "CREATE SECRET IF NOT EXISTS overture_s3 (TYPE s3, REGION 'us-west-2')"
        )
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


# Коннекторы читаются по расширенной области. Коннектор, на который ссылается
# сегмент внутри bbox, регулярно лежит за его границей: на участке
# 4x2 км в Берлине так теряется около тысячи из десяти тысяч нужных записей, а
# без их позиции граф не собирается (узел не на чем поставить). Расширение на
# ~1.1 км даёт полное покрытие на проверенном участке и не требует ни
# гигантского IN по тысячам id, ни повторных сканов parquet.
_CONNECTOR_BBOX_MARGIN_DEG = 0.01


def _dilated(bbox: tuple[float, float, float, float] | None) -> tuple[float, float, float, float] | None:
    if bbox is None:
        return None
    south, west, north, east = bbox
    margin = _CONNECTOR_BBOX_MARGIN_DEG
    # Округление убирает двоичный шум вида 39.309999999999995 из SQL.
    return (
        round(south - margin, 6), round(west - margin, 6),
        round(north + margin, 6), round(east + margin, 6),
    )


def _bbox_sql(
    bbox: tuple[float, float, float, float] | None,
) -> str:
    if bbox is None:
        return ""
    south, west, north, east = bbox
    return (
        f"AND bbox.xmin <= {east}"
        f" AND bbox.xmax >= {west}"
        f" AND bbox.ymin <= {north}"
        f" AND bbox.ymax >= {south}"
    )


def _sql_quote(value: str) -> str:
    return value.replace("'", "''")


# --- Выбор part-файлов через STAC ---------------------------------------
#
# Overture разложен по part-файлам без географического partitioning, поэтому
# чтение по glob `*` заставляет DuckDB открыть и опросить все ~128 частей
# темы. На замере в Тамбове это 175 с и сетевые сбои на середине чтения, тогда
# как STAC-индекс прямо говорит, что bbox пересекает ровно один файл: 7.5 с при
# том же результате. Индекс кэшируется на диске, а любая неудача молча
# возвращает None — тогда остаётся прежнее поведение через glob.
_STAC_HOSTS = (
    "https://overturemaps-extras-us-west-2.s3.us-west-2.amazonaws.com/stac",
    "https://overturemaps-extras-us-west-2.s3.amazonaws.com/stac",
    "https://s3.us-west-2.amazonaws.com/overturemaps-extras-us-west-2/stac",
    "https://stac.overturemaps.org",
)
_STAC_LOCK = Lock()


def _stac_index_path(release: str) -> Path | None:
    """Локальная копия STAC-индекса релиза; None, если он недоступен."""
    cache_dir = Path(tempfile.gettempdir()) / "transit_planner_overture"
    target = cache_dir / f"collections-{release}.parquet"
    if target.exists() and target.stat().st_size > 0:
        return target

    with _STAC_LOCK:
        if target.exists() and target.stat().st_size > 0:
            return target
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None

        for host in _STAC_HOSTS:
            url = f"{host}/{release}/collections.parquet"
            for attempt in range(3):
                try:
                    request = Request(url, headers={"User-Agent": "transit-planner"})
                    with urlopen(request, timeout=120) as response:
                        payload = response.read()
                    break
                except (OSError, HTTPError, URLError):
                    if attempt == 2:
                        payload = None
                    else:
                        time.sleep(2.0 * (attempt + 1))
            if payload:
                # Пишем через временный файл: прерванная закачка не должна
                # оставить индекс, который выглядит рабочим.
                staging = target.with_suffix(".part")
                try:
                    staging.write_bytes(payload)
                    staging.replace(target)
                except OSError:
                    return None
                return target
    return None


def _stac_part_files(
    release: str,
    overture_type: str,
    bbox: tuple[float, float, float, float] | None,
) -> list[str] | None:
    """part-файлы, пересекающие bbox, либо None, если индекс недоступен."""
    if bbox is None:
        return None
    index_path = _stac_index_path(release)
    if index_path is None:
        return None

    south, west, north, east = bbox
    sql = f"""
        SELECT assets
        FROM read_parquet('{_sql_quote(str(index_path))}')
        WHERE collection = '{_sql_quote(overture_type)}'
          AND type = 'Feature'
          AND bbox.xmin < {east}
          AND bbox.xmax > {west}
          AND bbox.ymin < {north}
          AND bbox.ymax > {south}
    """
    try:
        rows = _query_duckdb(sql)
    except Exception:
        return None

    hrefs: list[str] = []
    for (assets,) in rows:
        if not assets:
            continue
        entry = None
        if isinstance(assets, dict):
            entry = assets.get("aws") or assets.get("s3") or next(
                iter(assets.values()), None
            )
        if not isinstance(entry, dict):
            continue
        href = entry.get("href")
        if isinstance(href, str) and href:
            hrefs.append(href)
    return hrefs or None


def _parquet_source(
    default_glob: str,
    *,
    release: str,
    overture_type: str,
    bbox: tuple[float, float, float, float] | None,
) -> str:
    """Аргумент read_parquet: конкретные файлы из STAC либо прежний glob."""
    parts = _stac_part_files(release, overture_type, bbox)
    if not parts:
        return _sql_quote(default_glob)
    # В списочной форме каждый элемент должен быть строковым литералом.
    quoted = ", ".join("'" + _sql_quote(part) + "'" for part in parts)
    return "[" + quoted + "]"


def _parse_prohibited_transitions(
    raw,
    *,
    source_segment_id: str,
) -> tuple[ProhibitedTransition, ...]:
    if not raw:
        return ()

    result: list[ProhibitedTransition] = []
    for rule in raw:
        sequence_raw = _field(rule, "sequence") or ()
        sequence: list[ProhibitedTransitionSequenceEntry] = []
        for item in sequence_raw:
            segment_id = _field(item, "segment_id")
            connector_id = _field(item, "connector_id")
            if segment_id is None or connector_id is None:
                continue
            segment_text = str(segment_id)
            if not segment_text.startswith("overture:"):
                segment_text = f"overture:{segment_text}"
            sequence.append(
                ProhibitedTransitionSequenceEntry(
                    segment_id=segment_text,
                    connector_id=str(connector_id),
                )
            )

        if not sequence:
            continue

        when = _field(rule, "when")
        heading = _field(when, "heading")
        scoped = False
        for name in ("during", "mode", "using", "recognized", "vehicle"):
            value = _field(when, name)
            if value not in (None, (), [], {}, ""):
                scoped = True
                break
        if scoped:
            continue

        final_heading = _field(rule, "final_heading")
        final_heading = None if final_heading is None else str(final_heading).lower()
        heading = None if heading is None else str(heading).lower()

        if final_heading not in (None, "forward", "backward"):
            final_heading = None
        if heading not in (None, "forward", "backward"):
            heading = None

        result.append(
            ProhibitedTransition(
                source_segment_id=source_segment_id,
                sequence=tuple(sequence),
                final_heading=final_heading,
                when_heading=heading,
            )
        )

    return tuple(result)


def _parse_connector_refs(raw) -> tuple[ConnectorRef, ...]:
    if raw is None:
        return ()

    refs: list[ConnectorRef] = []
    for item in raw:
        connector_id = None
        at = None

        if isinstance(item, dict):
            connector_id = item.get("connector_id")
            at = item.get("at")
        elif hasattr(item, "keys"):
            connector_id = item["connector_id"]
            at = item["at"]
        elif hasattr(item, "connector_id"):
            connector_id = item.connector_id
            at = item.at
        elif isinstance(item, (tuple, list)) and len(item) >= 2:
            connector_id, at = item[0], item[1]

        if connector_id is None:
            continue
        refs.append(
            ConnectorRef(
                connector_id=str(connector_id),
                at=0.0 if at is None else float(at),
            )
        )

    refs.sort(key=lambda ref: ref.at)
    return tuple(refs)


def _field(value, name: str):
    if value is None:
        return None
    if isinstance(value, dict):
        return value.get(name)
    try:
        return value[name]
    except (KeyError, TypeError, IndexError):
        pass
    return getattr(value, name, None)


def _access_directions(access_restrictions) -> tuple[bool, bool]:
    if not access_restrictions:
        return True, True

    forward_allowed = True
    backward_allowed = True
    for rule in access_restrictions:
        if _field(rule, "between") not in (None, (), []):
            continue
        when = _field(rule, "when")
        scoped = False
        for name in ("during", "mode", "using", "recognized", "vehicle"):
            value = _field(when, name)
            if value not in (None, (), [], {}, ""):
                scoped = True
                break
        if scoped:
            continue

        access_type = str(_field(rule, "access_type") or "").lower()
        if access_type not in ("allowed", "denied"):
            continue
        value = access_type == "allowed"
        heading = _field(when, "heading")
        if heading is None:
            forward_allowed = value
            backward_allowed = value
        elif str(heading).lower() == "forward":
            forward_allowed = value
        elif str(heading).lower() == "backward":
            backward_allowed = value

    return forward_allowed, backward_allowed


def _is_oneway(access_restrictions) -> bool:
    forward_allowed, backward_allowed = _access_directions(access_restrictions)
    return forward_allowed and not backward_allowed


def _effective_speed_kph(speed_limits, class_speed_kph: float) -> float:
    """Explicit posted limit when present, otherwise the road-class planning speed.

    class_speed_kph is mandatory: there is no further default. A row that is
    neither limit-signed nor class-mapped must be rejected by the caller.
    """
    if not speed_limits:
        return class_speed_kph
    for rule in speed_limits:
        if _field(rule, "between") not in (None, (), []):
            continue
        when = _field(rule, "when")
        if when not in (None, {}, (), []):
            continue
        maximum = _field(rule, "max_speed")
        value = _field(maximum, "value")
        unit = str(_field(maximum, "unit") or "").lower()
        if value is None:
            continue
        speed = float(value)
        if "mph" in unit:
            speed *= 1.609344
        elif "m/s" in unit or unit.replace(" ", "") == "ms":
            speed *= 3.6
        if speed > 0:
            return speed
    return class_speed_kph


def _haversine_linestring_m(points: tuple[Point, ...]) -> float:
    if len(points) < 2:
        return 0.0

    earth_radius_m = 6_378_137.0
    total = 0.0
    for left, right in zip(points, points[1:]):
        lat1 = radians(left.y)
        lat2 = radians(right.y)
        dlat = lat2 - lat1
        dlon = radians(right.x - left.x)
        h = sin(dlat / 2.0) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2.0) ** 2
        total += 2.0 * earth_radius_m * asin(sqrt(min(1.0, h)))
    return total


def _class_speed(road_class: str) -> float:
    return {
        "motorway": 100.0,
        "trunk": 80.0,
        "primary": 60.0,
        "secondary": 50.0,
        "tertiary": 40.0,
        "unclassified": 30.0,
        "residential": 30.0,
        "living_street": 15.0,
        "service": 15.0,
    }.get(road_class, 30.0)
