from __future__ import annotations

import json
from dataclasses import dataclass
from math import asin, cos, radians, sin, sqrt

from .data import (
    ConnectorRecord,
    ConnectorRef,
    ProhibitedTransition,
    ProhibitedTransitionSequenceEntry,
    RoadRecord,
)
from .geo import LineString, Point
from .network import Stop

DEFAULT_RELEASE = "2026-09-23.1"
DEFAULT_S3_ROOT = "s3://overturemaps-us-west-2/release"


@dataclass(frozen=True, slots=True)
class OvertureSource:
    release: str = DEFAULT_RELEASE
    storage_root: str = DEFAULT_S3_ROOT
    transportation_glob: str | None = None
    connector_glob: str | None = None
    infrastructure_glob: str | None = None

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

    def infrastructure(self) -> str:
        if self.infrastructure_glob:
            return self.infrastructure_glob
        return (
            f"{self.storage_root}/{self.release}/"
            "theme=base/type=infrastructure/*"
        )


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
            source_id = f"overture:{road_id}"
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
                    oneway=bool(oneway) or _is_oneway(access_restrictions),
                    connectors=refs,
                    length_m=_haversine_linestring_m(points),
                    prohibited_transitions=restrictions,
                )
            )

        return tuple(roads)

    def load_graph(self):
        from .road_builder import build_topological_road_graph

        return build_topological_road_graph(self.load_roads())

    def _sql(self) -> str:
        classes = ", ".join(
            "'" + value.replace("'", "''") + "'"
            for value in self.road_classes
        )
        bbox_filter = _bbox_sql(self.bbox)

        return f"""
            SELECT
                id,
                ST_AsGeoJSON(ST_GeomFromWKB(geometry)) AS geojson,
                class,
                subclass,
                FALSE AS oneway,
                connectors,
                access_restrictions,
                speed_limits,
                prohibited_transitions
            FROM read_parquet('{_sql_quote(self.source.transportation_segments())}')
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
            coordinates = geometry.get("coordinates") or []
            if len(coordinates) < 2:
                continue
            connectors.append(
                ConnectorRecord(
                    id=str(connector_id),
                    location=Point(
                        float(coordinates[0]),
                        float(coordinates[1]),
                    ),
                )
            )

        return tuple(connectors)

    def _sql(self) -> str:
        bbox_filter = _bbox_sql(self.bbox)
        return f"""
            SELECT
                id,
                ST_AsGeoJSON(ST_GeomFromWKB(geometry)) AS geojson
            FROM read_parquet('{_sql_quote(self.source.transportation_connectors())}')
            WHERE TRUE
              {bbox_filter}
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
                ST_AsGeoJSON(ST_GeomFromWKB(geometry)) AS geojson,
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


def _when_has_only_heading(when, heading: str) -> bool:
    if when is None:
        return False
    if _field(when, "heading") != heading:
        return False
    for name in ("during", "mode", "using", "recognized", "vehicle"):
        value = _field(when, name)
        if value not in (None, (), [], ""):
            return False
    return True


def _is_oneway(access_restrictions) -> bool:
    if not access_restrictions:
        return False
    backward_denied = False
    backward_allowed = False
    for rule in access_restrictions:
        if _field(rule, "access_type") == "denied":
            if _when_has_only_heading(_field(rule, "when"), "backward"):
                backward_denied = True
        elif _field(rule, "access_type") == "allowed":
            if _when_has_only_heading(_field(rule, "when"), "backward"):
                backward_allowed = True
    return backward_denied and not backward_allowed


def _effective_speed_kph(speed_limits, fallback: float) -> float:
    if not speed_limits:
        return fallback
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
    return fallback


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
