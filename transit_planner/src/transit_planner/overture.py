from __future__ import annotations

import json
from dataclasses import dataclass

from .data import RoadRecord
from .geo import LineString, Point
from .network import Stop

DEFAULT_RELEASE = "2026-09-23.1"
DEFAULT_S3_ROOT = "s3://overturemaps-us-west-2/release"


@dataclass(frozen=True, slots=True)
class OvertureSource:
    release: str = DEFAULT_RELEASE
    storage_root: str = DEFAULT_S3_ROOT
    transportation_glob: str | None = None
    infrastructure_glob: str | None = None

    def transportation_segments(self) -> str:
        if self.transportation_glob:
            return self.transportation_glob
        return (
            f"{self.storage_root}/{self.release}/"
            "theme=transportation/type=segment/*.parquet"
        )

    def infrastructure(self) -> str:
        if self.infrastructure_glob:
            return self.infrastructure_glob
        return (
            f"{self.storage_root}/{self.release}/"
            "theme=base/type=infrastructure/*.parquet"
        )


class OvertureTransportationProvider:
    """Read Overture transportation road segments through DuckDB."""

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

        for road_id, geojson, segment_class, subclass, oneway in rows:
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
            road_type = str(segment_class or subclass or "unknown")
            roads.append(
                RoadRecord(
                    id=f"overture:{road_id}",
                    geometry=LineString(points),
                    speed_kph=_class_speed(road_type),
                    road_type=road_type,
                    oneway=bool(oneway),
                )
            )

        return tuple(roads)

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
                FALSE AS oneway
            FROM read_parquet('{_sql_quote(self.source.transportation_segments())}')
            WHERE subtype = 'road'
              AND (
                class IN ({classes})
                OR subclass IN ({classes})
              )
              {bbox_filter}
        """


class OvertureTransitProvider:
    """Read Overture base-theme transit infrastructure.

    Overture deliberately puts bus stops, platforms, stations and similar
    intermediate waypoints in base/infrastructure with subtype='transit'.
    """

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
            coordinates = geometry.get("coordinates") or []
            if len(coordinates) < 2:
                continue

            lon, lat = coordinates[:2]
            stops[f"overture:{stop_id}"] = Stop(
                id=f"overture:{stop_id}",
                name=str(name or stop_class or stop_id),
                location=Point(float(lon), float(lat)),
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
            FROM read_parquet('{_sql_quote(self.source.infrastructure())}')
            WHERE subtype = 'transit'
              AND class IN ({classes})
              {bbox_filter}
        """


def _query_duckdb(sql: str):
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError(
            "Overture providers require duckdb"
        ) from exc

    connection = duckdb.connect()
    try:
        connection.execute("INSTALL httpfs; LOAD httpfs;")
        connection.execute("INSTALL spatial; LOAD spatial;")
        connection.execute("SET s3_region='us-west-2'")
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


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
