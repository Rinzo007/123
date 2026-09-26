from __future__ import annotations

import json
from dataclasses import dataclass

from .data import RoadRecord
from .geo import LineString, Point

DEFAULT_RELEASE = "2026-09-23.0"
DEFAULT_AZURE_ROOT = "https://overturemapswestus2.blob.core.windows.net/release"


@dataclass(frozen=True, slots=True)
class OvertureSource:
    release: str = DEFAULT_RELEASE
    parquet_glob: str | None = None

    def segment_glob(self) -> str:
        if self.parquet_glob:
            return self.parquet_glob
        return (
            f"{DEFAULT_AZURE_ROOT}/{self.release}/"
            "theme=transportation/type=segment/*.parquet"
        )


class OvertureTransportationProvider:
    """Lazy DuckDB reader for Overture transportation road segments."""

    def __init__(
        self,
        *,
        source: OvertureSource = OvertureSource(),
        bbox: tuple[float, float, float, float] | None = None,
        road_classes: tuple[str, ...] = (
            "motorway", "trunk", "primary", "secondary", "tertiary",
            "residential", "service",
        ),
    ) -> None:
        self.source = source
        self.bbox = bbox
        self.road_classes = road_classes

    def load_roads(self) -> tuple[RoadRecord, ...]:
        try:
            import duckdb
        except ImportError as exc:
            raise RuntimeError(
                "OvertureTransportationProvider requires duckdb"
            ) from exc

        connection = duckdb.connect()
        try:
            connection.execute("INSTALL httpfs; LOAD httpfs;")
            connection.execute("INSTALL spatial; LOAD spatial;")
            rows = connection.execute(self._sql()).fetchall()
        finally:
            connection.close()

        roads: list[RoadRecord] = []
        for row in rows:
            road_id, geojson, subclass, segment_class = row
            if not geojson:
                continue
            geometry = json.loads(geojson)
            coords = geometry.get("coordinates") or []
            if len(coords) < 2:
                continue
            points = tuple(Point(float(x), float(y)) for x, y, *_ in coords)
            road_type = str(segment_class or subclass or "unknown")
            roads.append(
                RoadRecord(
                    id=f"overture:{road_id}",
                    geometry=LineString(points),
                    speed_kph=_class_speed(road_type),
                    road_type=road_type,
                    oneway=False,
                )
            )
        return tuple(roads)

    def _sql(self) -> str:
        classes = ", ".join("'" + value.replace("'", "''") + "'" for value in self.road_classes)
        bbox_filter = ""
        if self.bbox:
            south, west, north, east = self.bbox
            bbox_filter = (
                f" AND bbox.ymin <= {north} AND bbox.ymax >= {south}"
                f" AND bbox.xmin <= {east} AND bbox.xmax >= {west}"
            )
        return f"""
            SELECT
                id,
                ST_AsGeoJSON(ST_GeomFromWKB(geometry)) AS geojson,
                subclass,
                class
            FROM read_parquet('{self.source.segment_glob()}')
            WHERE type = 'segment'
              AND subtype = 'road'
              AND (class IN ({classes}) OR subclass IN ({classes}))
              {bbox_filter}
        """


def _class_speed(road_class: str) -> float:
    return {
        "motorway": 100.0,
        "trunk": 80.0,
        "primary": 60.0,
        "secondary": 50.0,
        "tertiary": 40.0,
        "residential": 30.0,
        "service": 15.0,
    }.get(road_class, 30.0)
