"""Транспортные зоны, районы и веса зон по растру/файлам.

Сетка ячеек внутри границы (``build_zones``), названия ближайших районов,
взвешивание по GHS-растру и чтение зон из внешней модели Tranmodel.
"""

from __future__ import annotations

import contextlib
import math
from pathlib import Path
from typing import Any

import numpy as np
from scipy.spatial import cKDTree
from shapely.geometry import Point, box
from shapely.ops import transform as geom_transform

from common import open_raster_index, raster_stack, resolve_sources
from ..model import (
    _KILOMETERS_PER_DEGREE,
    OdMatrixError,
    Zones,
    _cosscale,
    haversine_km,
)

__all__ = [
    "assign_district_names",
    "build_zones",
    "load_districts",
    "load_zones_from_file",
    "zonal_weights",
]

_DEFAULT_ZONE_SIZE_M = 750.0
_MIN_CLIP_AREA_RATIO = 0.3
_GEO_PAD_DEG = 0.001
_PROJECTED_PAD_M = 50.0
_MAX_DISTRICT_RADIUS_M = 30000.0


def _cell_step(size_m: float, mean_lat: float) -> tuple[float, float]:
    lat_deg = float(size_m) / _KILOMETERS_PER_DEGREE / 1000.0
    lon_deg = lat_deg / max(math.cos(math.radians(mean_lat)), 0.2)
    return lon_deg, lat_deg


def build_zones(boundary: Any, *, size_m: float = _DEFAULT_ZONE_SIZE_M) -> Zones:
    """Строит сетку ячеек внутри границы (целые/обрезанные по границе).

    Координаты ячеек выводятся от угла границы целыми шагами сетки
    (``minx + col*lon_deg``), чтобы многократное ``x += lon_deg`` не
    накапливало дрейф плавающей точки на больших сетках.
    """
    minx, miny, maxx, maxy = boundary.bounds
    lon_deg, lat_deg = _cell_step(size_m, (miny + maxy) / 2.0)
    min_area = _MIN_CLIP_AREA_RATIO * lon_deg * lat_deg
    polygons: list[Any] = []
    row = 0
    while True:
        y = miny + row * lat_deg
        if y >= maxy:
            break
        col = 0
        while True:
            x = minx + col * lon_deg
            if x >= maxx:
                break
            clip = box(x, y, x + lon_deg, y + lat_deg).intersection(boundary)
            if not clip.is_empty and clip.area >= min_area:
                polygons.append(clip)
            col += 1
        row += 1
    if not polygons:
        raise OdMatrixError("Зонирование не дало ни одной зоны внутри границы")
    ids = np.arange(1, len(polygons) + 1, dtype=np.int64)
    xy = np.asarray([(p.centroid.x, p.centroid.y) for p in polygons], dtype=float)
    return Zones(ids=ids, polygons=tuple(polygons), xy=xy, bounds=boundary.bounds)


def load_districts(path: str | Path) -> list[tuple[str, Any]]:
    """Загружает районы/места: ``[{'n': name, 'x': lon, 'y': lat}]``
    (формат page_assets districts.json) либо GeoJSON FeatureCollection.
    Возвращает ``[(имя, геометрия), ...]`` без дубликатов имён.
    """
    import json as _json

    data = _json.loads(Path(path).read_text(encoding="utf-8"))
    places: list[tuple[str, Any]] = []
    if isinstance(data, dict) and isinstance(data.get("places"), list):
        for item in data["places"]:
            name = str(item.get("n") or item.get("name") or "").strip()
            if not name:
                continue
            if "x" in item and "y" in item:
                geometry = Point(float(item["x"]), float(item["y"]))
            else:
                continue
            places.append((name, geometry))
    elif isinstance(data, dict) and data.get("type") == "FeatureCollection":
        for feature in data.get("features", []):
            props = feature.get("properties") or {}
            name = str(props.get("name") or props.get("NAME") or "").strip()
            geometry = feature.get("geometry")
            if not name or geometry is None:
                continue
            places.append((name, geometry))
    seen: set[str] = set()
    unique: list[tuple[str, Any]] = []
    for name, geometry in places:
        if name in seen:
            continue
        seen.add(name)
        unique.append((name, geometry))
    if not unique:
        raise OdMatrixError(f"Файл районов {path} не содержит ни одного места")
    return unique


def _representative_point(geometry: Any) -> Point | None:
    """Точка для поиска: Point — сама, LineString/Polygon — представитель."""
    import shapely.geometry as sg

    if geometry is None or geometry.is_empty:
        return None
    if geometry.geom_type in ("Point",):
        return geometry
    if geometry.geom_type in ("LineString", "MultiLineString"):
        pts = list(geometry.coords) if geometry.geom_type == "LineString" else [
            p for part in geometry.geoms for p in part.coords
        ]
        if not pts:
            return None
        mid = pts[len(pts) // 2]
        return sg.Point(mid)
    if geometry.geom_type in ("Polygon", "MultiPolygon"):
        return geometry.representative_point()
    return None


def assign_district_names(zones: Zones, places: list[tuple[str, Any]]) -> tuple[str, ...]:
    """Имена ближайших районов для каждой зоны (пустая строка, если ближе
    ``_MAX_DISTRICT_RADIUS_M`` ничего нет)."""
    points: list[tuple[float, float]] = []
    names: list[str] = []
    for name, geometry in places:
        rep = _representative_point(geometry)
        if rep is not None and rep.is_empty is False:
            points.append((rep.x, rep.y))
            names.append(name)
    if not points:
        return tuple("" for _ in range(len(zones)))
    array = np.asarray(points, dtype=float)
    scale = _cosscale(array, zones.xy)
    tree = cKDTree(array * [scale, 1.0])
    _, nearest = tree.query(zones.xy * [scale, 1.0], k=1)
    result: list[str] = []
    for i in range(len(zones)):
        x1, y1 = zones.xy[i]
        idx = int(nearest[i])
        x2, y2 = points[idx]
        dist_m = haversine_km((x1, y1), (x2, y2)) * 1000.0
        result.append(names[idx] if dist_m <= _MAX_DISTRICT_RADIUS_M else "")
    return tuple(result)


def _tile_is_geographic(tile: dict[str, Any]) -> bool:
    return bool(getattr(tile.get("crs"), "is_geographic", False))


def _tile_bounds_in(
    tile: dict[str, Any], warp: Any, bounds: tuple[float, float, float, float]
) -> tuple[float, float, float, float]:
    if _tile_is_geographic(tile):
        return bounds
    xs, ys = warp.transform(
        "EPSG:4326",
        tile["crs"],
        [bounds[0], bounds[2], bounds[0], bounds[2]],
        [bounds[1], bounds[1], bounds[3], bounds[3]],
    )
    return (min(xs), min(ys), max(xs), max(ys))


def _tile_overlaps_bounds(
    bounds: tuple[float, float, float, float], tile: dict[str, Any]
) -> bool:
    tile_bounds = tile.get("bounds")
    return not (
        bounds[0] > tile_bounds.right
        or bounds[2] < tile_bounds.left
        or bounds[1] > tile_bounds.top
        or bounds[3] < tile_bounds.bottom
    )


def _reproject_polygon(poly: Any, warp: Any, target_crs: Any) -> Any:
    def _apply(x: Any, y: Any) -> tuple[Any, Any]:
        tx, ty = warp.transform("EPSG:4326", target_crs, x.tolist(), y.tolist())
        return np.asarray(tx), np.asarray(ty)

    return geom_transform(_apply, poly)


def _zone_tile_sums(
    zones: Zones, tile: dict[str, Any], *, stack: dict[str, Any]
) -> np.ndarray:
    """Вклад одного тайла: сумма значений растра внутри каждой зоны."""
    rio_windows = stack["windows"]
    rio_features = stack["features"]
    warp = stack["warp"]
    ds = tile["ds"]
    inner = _tile_bounds_in(tile, warp, zones.bounds)
    if not _tile_overlaps_bounds(inner, tile):
        return np.zeros(len(zones))
    is_geo = _tile_is_geographic(tile)
    pad = _GEO_PAD_DEG if is_geo else _PROJECTED_PAD_M
    win = rio_windows.from_bounds(
        inner[0] - pad, inner[1] - pad, inner[2] + pad, inner[3] + pad, ds.transform
    )
    win = win.round_lengths().round_offsets()
    full = rio_windows.Window(0, 0, ds.width, ds.height)
    win = rio_windows.intersection(win, full)
    if win is None or win.width <= 0 or win.height <= 0:
        return np.zeros(len(zones))
    arr = ds.read(1, window=win).astype("float64")
    win_transform = rio_windows.transform(win, ds.transform)
    shapes = []
    for i, poly in enumerate(zones.polygons, start=1):
        target = poly if is_geo else _reproject_polygon(poly, warp, tile["crs"])
        shapes.append((target, i))
    labels = rio_features.rasterize(
        shapes,
        out_shape=(int(win.height), int(win.width)),
        transform=win_transform,
        fill=0,
    )
    flat = arr.reshape(-1)
    names = labels.astype(np.int64).reshape(-1)
    good = np.isfinite(flat) & (flat > 0.0)
    sums = np.bincount(names[good], weights=flat[good], minlength=len(zones) + 1)
    return sums[1:]


def zonal_weights(
    zones: Zones, raster_path: str | None, *, stack: dict[str, Any] | None = None
) -> np.ndarray:
    """Веса зон: сумма значений GHS-растра внутри зоны; без растра — единицы."""
    stack = stack or raster_stack()
    rasterio = stack["rasterio"]
    paths = resolve_sources(raster_path, (".tif", ".tiff"))
    if not paths:
        return np.ones(len(zones))
    index = open_raster_index(paths, rasterio)
    if not index:
        return np.ones(len(zones))
    total = np.zeros(len(zones), dtype=np.float64)
    try:
        for tile in index:
            total += _zone_tile_sums(zones, tile, stack=stack)
    finally:
        for tile in index:
            with contextlib.suppress(OSError, ValueError, RuntimeError):
                tile["ds"].close()
    if float(total.sum()) <= 0.0:
        return np.ones(len(zones))
    return total


def load_zones_from_file(
    zones_path: str | Path,
    *,
    reporter: Any = None,
) -> tuple[Zones, np.ndarray, np.ndarray]:
    """Загружает зоны и веса из файла внешней модели (формат Tranmodel).

    Возвращает ``(zones, production, attraction)``: отправления берутся из
    колонки ``production`` (или ``population``), притяжение — из ``attraction``
    (или ``jobs``). Геометрия перепроецируется в EPSG:4326, центроиды считаются
    в исходной (метрической) проекции.
    """
    import geopandas as gpd

    line = getattr(reporter, "line", None)
    if line:
        line(f"  Загрузка зон из файла: {zones_path}")
    if Path(zones_path).suffix.lower() == ".csv":
        # CSV-фолбэк `_write_frame`: геометрия записана WKT-строкой.
        import pandas as pd

        zdf = pd.read_csv(zones_path)
        if "geometry" not in zdf.columns:
            raise OdMatrixError(
                f"Файл зон {zones_path} не содержит колонки geometry"
            )
        geometry = gpd.GeoSeries.from_wkt(zdf["geometry"])
        zdf = gpd.GeoDataFrame(
            zdf.drop(columns=["geometry"]),
            geometry=geometry,
            crs="EPSG:4326",
        )
    else:
        zdf = gpd.read_parquet(zones_path)
    zdf = zdf.sort_values("zone_id").reset_index(drop=True)
    ids = zdf.zone_id.to_numpy(dtype=np.int64)
    center_crs = zdf.crs if zdf.crs is not None else 4326
    # Центроиды считаем в метрической проекции (точнее), исходную не трогаем.
    # Для географического CRS подбираем локальную UTM-зону по средней долготе,
    # а полушарие — по средней широте (326xx — север, 327xx — юг).
    if bool(getattr(center_crs, "is_geographic", False)):
        minx, miny, maxx, maxy = zdf.total_bounds
        mean_lon = float((minx + maxx) / 2.0)
        mean_lat = float((miny + maxy) / 2.0)
        zone = int((mean_lon + 180.0) // 6.0) + 1
        zone = min(max(zone, 1), 60)
        utm_epsg = 32600 + zone if mean_lat >= 0.0 else 32700 + zone
        try:
            projected = zdf.to_crs(f"EPSG:{utm_epsg}")
            projected_centers = projected.geometry.centroid
            centers_geo = projected_centers.to_crs(4326)
        except (ValueError, RuntimeError):
            centers_geo = zdf.geometry.centroid
    else:
        projected_centers = zdf.geometry.centroid
        centers_geo = gpd.GeoSeries(projected_centers, crs=center_crs).to_crs(4326)
    zdf = zdf.to_crs(4326)
    polygons = tuple(zdf.geometry.tolist())
    xy = np.column_stack([centers_geo.x.to_numpy(), centers_geo.y.to_numpy()])
    minx, miny, maxx, maxy = zdf.total_bounds
    zones = Zones(
        ids=ids,
        polygons=polygons,
        xy=xy,
        bounds=(float(minx), float(miny), float(maxx), float(maxy)),
    )
    if "production" in zdf.columns:
        production = zdf.production.to_numpy(dtype=np.float64)
    elif "population" in zdf.columns:
        production = zdf.population.to_numpy(dtype=np.float64)
    else:
        production = np.ones(len(ids), dtype=np.float64)
    if "attraction" in zdf.columns:
        attraction = zdf.attraction.to_numpy(dtype=np.float64)
    elif "jobs" in zdf.columns:
        attraction = zdf.jobs.to_numpy(dtype=np.float64)
    else:
        attraction = production.copy()
    return zones, production, attraction