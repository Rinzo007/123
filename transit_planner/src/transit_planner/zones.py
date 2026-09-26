from __future__ import annotations

from dataclasses import dataclass
from math import cos, radians, sqrt
from typing import Iterable

from .city import DemandZone
from .geo import Point


@dataclass(frozen=True, slots=True)
class ZoneCell:
    id: str
    min_x: float
    min_y: float
    max_x: float
    max_y: float
    population: float = 0.0
    jobs: float = 0.0

    @property
    def centroid(self) -> tuple[float, float]:
        return ((self.min_x + self.max_x) / 2.0, (self.min_y + self.max_y) / 2.0)


def generate_grid_zones(
    min_x: float,
    min_y: float,
    max_x: float,
    max_y: float,
    *,
    cell_size: float,
    population_points: Iterable[tuple[float, float, float]] = (),
    job_points: Iterable[tuple[float, float, float]] = (),
) -> tuple[DemandZone, ...]:
    if cell_size <= 0:
        raise ValueError("cell_size must be positive")
    if min_x >= max_x or min_y >= max_y:
        raise ValueError("Invalid zone extent")

    population_points = tuple(population_points)
    job_points = tuple(job_points)

    zones: list[DemandZone] = []
    y = min_y
    row = 0
    while y < max_y:
        x = min_x
        col = 0
        top = min(y + cell_size, max_y)
        while x < max_x:
            right = min(x + cell_size, max_x)
            population = _aggregate_points(population_points, x, y, right, top)
            jobs = _aggregate_points(job_points, x, y, right, top)
            zones.append(
                DemandZone(
                    id=f"z{row:04d}_{col:04d}",
                    centroid_x=(x + right) / 2.0,
                    centroid_y=(y + top) / 2.0,
                    population=population,
                    jobs=jobs,
                )
            )
            x = right
            col += 1
        y = top
        row += 1
    return tuple(zones)


def nearest_zone(
    zones: Iterable[DemandZone], x: float, y: float
) -> DemandZone | None:
    best: DemandZone | None = None
    best_distance = float("inf")
    for zone in zones:
        distance = sqrt((zone.centroid_x - x) ** 2 + (zone.centroid_y - y) ** 2)
        if distance < best_distance:
            best_distance = distance
            best = zone
    return best


def _aggregate_points(
    points: Iterable[tuple[float, float, float]],
    min_x: float,
    min_y: float,
    max_x: float,
    max_y: float,
) -> float:
    total = 0.0
    for x, y, value in points:
        if min_x <= x < max_x and min_y <= y < max_y:
            total += max(0.0, float(value))
    return total


def generate_zones_from_population_raster(
    raster_path: str,
    *,
    cell_size_m: float | None = None,
    jobs_points: Iterable[tuple[float, float, float]] = (),
    origin_lon: float | None = None,
    origin_lat: float | None = None,
) -> tuple[DemandZone, ...]:
    try:
        import rasterio
    except ImportError as exc:
        raise RuntimeError(
            "Population raster support requires the optional 'gis' extra"
        ) from exc

    from .projection import EARTH_RADIUS_M, project_wgs84_point

    with rasterio.open(raster_path) as dataset:
        if dataset.crs is None:
            raise ValueError("Population raster must define a CRS")
        if dataset.crs.to_epsg() != 4326:
            raise ValueError("Population raster must use EPSG:4326")

        transform = dataset.transform
        rows, cols = dataset.height, dataset.width
        center_lon = (
            float(origin_lon)
            if origin_lon is not None
            else float((dataset.bounds.left + dataset.bounds.right) / 2.0)
        )
        center_lat = (
            float(origin_lat)
            if origin_lat is not None
            else float((dataset.bounds.bottom + dataset.bounds.top) / 2.0)
        )

        resolution_x_deg = abs(transform.a)
        resolution_y_deg = abs(transform.e)
        meters_per_degree_lon = EARTH_RADIUS_M * cos(radians(center_lat)) * radians(1.0)
        meters_per_degree_lat = EARTH_RADIUS_M * radians(1.0)
        native_x_m = resolution_x_deg * meters_per_degree_lon
        native_y_m = resolution_y_deg * meters_per_degree_lat

        target_size = (
            max(native_x_m, native_y_m)
            if cell_size_m is None
            else float(cell_size_m)
        )
        if target_size <= 0:
            raise ValueError("cell_size_m must be positive")

        step_x = max(1, int(round(target_size / max(native_x_m, 1e-9))))
        step_y = max(1, int(round(target_size / max(native_y_m, 1e-9))))
        data = dataset.read(1, masked=True)
        raster_transform = transform

    zones: list[DemandZone] = []
    jobs_points = tuple(jobs_points)
    row_index = 0

    for row_start in range(0, rows, step_y):
        row_stop = min(rows, row_start + step_y)
        for col_start in range(0, cols, step_x):
            col_stop = min(cols, col_start + step_x)
            window = data[row_start:row_stop, col_start:col_stop]
            population = float(window.sum()) if window.count() else 0.0

            left, top = rasterio.transform.xy(
                raster_transform,
                row_start,
                col_start,
                offset="ul",
            )
            right, bottom = rasterio.transform.xy(
                raster_transform,
                row_stop - 1,
                col_stop - 1,
                offset="lr",
            )
            min_lon = min(left, right)
            max_lon = max(left, right)
            min_lat = min(bottom, top)
            max_lat = max(bottom, top)
            jobs = _aggregate_points(
                jobs_points,
                min_lon,
                min_lat,
                max_lon,
                max_lat,
            )

            if population <= 0.0 and jobs <= 0.0:
                continue

            centroid = project_wgs84_point(
                Point(
                    (min_lon + max_lon) / 2.0,
                    (min_lat + max_lat) / 2.0,
                ),
                origin_lon=center_lon,
                origin_lat=center_lat,
            )
            zones.append(
                DemandZone(
                    id=f"raster_{row_index:06d}",
                    centroid_x=centroid.x,
                    centroid_y=centroid.y,
                    population=max(0.0, population),
                    jobs=max(0.0, jobs),
                )
            )
            row_index += 1

    return tuple(zones)
