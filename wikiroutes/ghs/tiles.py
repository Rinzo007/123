"""GHS: работа с тайлами, буферами и чтением растров (низкий уровень)."""
from __future__ import annotations

import atexit
import collections
import contextlib
import logging
import threading
from collections.abc import Sequence
from typing import Any

import numpy as np
from shapely import STRtree
from shapely.geometry import box

from ..common import (
    _open_raster_quiet,
    geometry_mask_quiet,
    raster_stack,
    shapely_stack,
    union_all,
)
from ..models import RouteData

logger = logging.getLogger("wikiroutes.gis.ghs")

_thread_local = threading.local()


def _get_thread_local_ds(path: str, rasterio_module: Any) -> Any:
    if not hasattr(_thread_local, "datasets"):
        _thread_local.datasets = collections.OrderedDict()
    cache = _thread_local.datasets
    if path in cache:
        cache.move_to_end(path)
        return cache[path]
    ds = _open_raster_quiet(path, rasterio_module, sharing=False)
    if ds is None:
        return None
    cache[path] = ds
    while len(cache) > GHS_MAX_OPEN_DATASETS:
        _, old_ds = cache.popitem(last=False)
        with contextlib.suppress(Exception):
            old_ds.close()
    return ds


def _close_thread_local_datasets() -> None:
    if hasattr(_thread_local, "datasets"):
        for ds in _thread_local.datasets.values():
            with contextlib.suppress(Exception):
                ds.close()
        _thread_local.datasets.clear()


atexit.register(_close_thread_local_datasets)


def _tile_overlaps_bounds(xs: Sequence[float], ys: Sequence[float], bounds: Any) -> bool:
    """Пересекается ли трансформированный bbox с границами тайла."""
    return not (
        max(xs) < bounds.left
        or min(xs) > bounds.right
        or max(ys) < bounds.bottom
        or min(ys) > bounds.top
    )


def _filter_tiles_by_bbox(
    index: list[dict[str, Any]],
    min_lon: float,
    max_lon: float,
    min_lat: float,
    max_lat: float,
    warp: Any,
) -> list[dict[str, Any]]:
    candidates = []
    for tile in index:
        try:
            xs, ys = warp.transform(
                "EPSG:4326",
                tile["crs"],
                [min_lon, max_lon, min_lon, max_lon],
                [min_lat, min_lat, max_lat, max_lat],
            )
            if _tile_overlaps_bounds(xs, ys, tile["bounds"]):
                candidates.append(tile)
        except Exception:  # noqa: BLE001 — кривые тайлы не должны ронять фильтрацию
            candidates.append(tile)
    return candidates or index


class SharedTileIndex:
    __slots__ = ("tiles", "tree")

    def __init__(self, tiles: list[dict[str, Any]]) -> None:
        self.tiles = tiles
        self.tree: STRtree | None = (
            STRtree(
                [
                    box(
                        tile["bounds"].left,
                        tile["bounds"].bottom,
                        tile["bounds"].right,
                        tile["bounds"].top,
                    )
                    for tile in tiles
                ]
            )
            if tiles
            else None
        )

    def tiles_intersecting_bbox(
        self,
        min_lon: float,
        min_lat: float,
        max_lon: float,
        max_lat: float,
    ) -> list[dict[str, Any]]:
        if not self.tiles:
            return []
        if self.tree is None:
            return list(self.tiles)
        indices = self.tree.query(
            box(min_lon, min_lat, max_lon, max_lat),
            predicate="intersects",
        )
        return [self.tiles[int(i)] for i in indices]


def _crs_is_geographic(ds: Any) -> bool:
    try:
        return bool(getattr(ds.crs, "is_geographic", False))
    except Exception:  # noqa: BLE001 — CRS может отсутствовать
        return False


def _build_tile_window(ds: Any, poly: Any, is_geo: bool, rio_windows: Any) -> Any:
    """Окно ридинга по bounds полигона; None при отсутствии пересечения."""
    pad = 0.001 if is_geo else 50
    minx, miny, maxx, maxy = poly.bounds
    win = rio_windows.from_bounds(minx - pad, miny - pad, maxx + pad, maxy + pad, ds.transform)
    win = win.round_lengths().round_offsets()
    full_win = rio_windows.Window(0, 0, ds.width, ds.height)
    win = rio_windows.intersection(win, full_win)
    if win is None or win.width <= 0 or win.height <= 0:
        return None
    return win


def _clean_vals(arr: np.ndarray, nodata: Any, max_val: float) -> None:
    """Помечает шумовые значения как NaN (mutates ``arr``)."""
    nodata = nodata if nodata is not None else 4294967295.0
    arr[arr == nodata] = np.nan
    arr[~np.isfinite(arr)] = np.nan
    arr[arr < 0] = np.nan
    arr[arr > max_val] = np.nan


def _filtered_values(arr: np.ndarray, mask: Any, np_: Any) -> np.ndarray:
    vals = arr[mask]
    return vals[np_.isfinite(vals)]


def _read_and_mask_tile(
    tile: dict[str, Any],
    poly: Any,
    max_val: float,
    rasterio: Any,
    np: Any,
    rio_features: Any,
    rio_windows: Any,
) -> np.ndarray | None:
    ds = _get_thread_local_ds(tile["path"], rasterio)
    if ds is None:
        logger.warning("Тайл недоступен (ошибка открытия): %s", tile.get("path", "?"))
        return None
    try:
        if poly is None or poly.is_empty:
            return None
        is_geo = _crs_is_geographic(ds)
        win = _build_tile_window(ds, poly, is_geo, rio_windows)
        if win is None:
            return None
        arr = ds.read(1, window=win).astype("float64")
        _clean_vals(arr, ds.nodata, max_val)
        win_transform = rio_windows.transform(win, ds.transform)
        mask = geometry_mask_quiet(
            rasterio, rio_features, [poly],
            out_shape=(int(win.height), int(win.width)),
            transform=win_transform, all_touched=False, invert=True,
        )
        if np.count_nonzero(mask) == 0:
            return None
        vals = _filtered_values(arr, mask, np)
        if len(vals) == 0:
            return None
        return vals
    except Exception as exc:  # noqa: BLE001 — повреждённый тайл не роняет маршрут
        cause = exc.__cause__ or exc.__context__
        detail = f"{type(cause).__name__}: {cause}" if cause else str(exc)
        logger.warning("Ошибка тайла %s: %s (%s)", tile.get("path", "?"), exc, detail)
        return None


def _extract_route_bounds(
    dirs: list[Any],
    stop_coords: list[tuple[float, float]],
    Point: Any,
) -> tuple[tuple[float, float, float, float], bool] | tuple[None, bool]:
    """Bbox маршрута (по остановкам или геометрии); None если геометрии нет.

    Возвращает ``(bounds, use_stops)``: bounds = (min_lat, max_lat, min_lon,
    max_lon).
    """
    use_stops = len(stop_coords) > 0 and Point is not None
    if use_stops:
        lats_s = [lat for lat, _ in stop_coords]
        lons_s = [lon for _, lon in stop_coords]
        return (min(lats_s), max(lats_s), min(lons_s), max(lons_s)), True
    coords = [coord for direction in dirs for coord in direction.coords]
    if not coords:
        return None, False
    lats = [lat for lat, _ in coords]
    lons = [lon for _, lon in coords]
    return (min(lats), max(lats), min(lons), max(lons)), False


def _select_candidates(
    min_lon: float,
    min_lat: float,
    max_lon: float,
    max_lat: float,
    index: list[dict[str, Any]],
    warp: Any,
    shared: SharedTileIndex | None,
) -> tuple[list[dict[str, Any]], STRtree | None]:
    """Тайлы-кандидаты и STRtree (общий или локально построенный)."""
    if shared is not None:
        candidate_tiles = shared.tiles_intersecting_bbox(min_lon, min_lat, max_lon, max_lat)
        return candidate_tiles, shared.tree
    candidate_tiles = _filter_tiles_by_bbox(index, min_lon, max_lon, min_lat, max_lat, warp)
    tile_geoms = []
    for tile in candidate_tiles:
        b = tile["bounds"]
        tile_geoms.append(box(b.left, b.bottom, b.right, b.top))
    tree = STRtree(tile_geoms) if tile_geoms else None
    return candidate_tiles, tree


def _unified_result(
    buffer_poly: Any,
    corridor_m2: float,
    use_stops: bool,
    dirs: list[Any],
    target_crs: Any,
    buffer_m: float,
    warp: Any,
    Point: Any,
    LineString: Any,
    union_all_func: Any,
) -> tuple[Any, float]:
    """Строит единый буфер по маршруту (остановки/геометрия совмещённо)."""
    if use_stops:
        buffer_poly, corridor_m2 = _build_stops_unified_buffer(
            dirs, target_crs, buffer_m, warp, Point, union_all_func
        )
    if buffer_poly is None:
        buffer_poly, corridor_m2 = _build_unified_buffer(
            dirs, target_crs, buffer_m, warp, LineString, union_all_func
        )
    return buffer_poly, corridor_m2


def _unified_route_sum(
    shared: SharedTileIndex | None,
    candidate_tiles: list[dict[str, Any]],
    tree: STRtree | None,
    buffer_poly: Any,
    corridor_m2: float,
    max_val: float,
    rasterio: Any,
    np: Any,
    rio_features: Any,
    rio_windows: Any,
) -> tuple[float, float, int]:
    """Сумма по единому буферу маршрута (STRtree из shared либо per-кандидатный)."""
    tiles_for_tree = shared.tiles if shared is not None else candidate_tiles
    total_value, _, tiles_used = _compute_with_unified_buffer(
        tiles_for_tree, tree, buffer_poly, max_val, rasterio, np, rio_features, rio_windows
    )
    return total_value, corridor_m2, tiles_used


def _stops_or_line_sum(
    tiles: list[dict[str, Any]],
    dirs: list[Any],
    buffer_m: float,
    max_val: float,
    warp: Any,
    use_stops: bool,
    Point: Any,
    LineString: Any,
    union_all_func: Any,
    np: Any,
    rasterio: Any,
    rio_features: Any,
    rio_windows: Any,
    tree: STRtree | None,
) -> tuple[float, float, int]:
    """Сумма по тайл-локальным буферам: стопы с фолбэком на геометрию либо геометрия."""
    if use_stops and Point is not None:
        total_value, corridor_m2, tiles_used = _compute_with_per_tile_stops_buffer(
            tiles, dirs, buffer_m, max_val, warp, Point, union_all_func, rasterio, np, rio_features, rio_windows, tree
        )
        if tiles_used == 0 and corridor_m2 == 0.0:
            return _compute_with_per_tile_buffer(
                tiles, dirs, buffer_m, max_val, warp, LineString, union_all_func, rasterio, np, rio_features, rio_windows, tree
            )
        return total_value, corridor_m2, tiles_used
    return _compute_with_per_tile_buffer(
        tiles, dirs, buffer_m, max_val, warp, LineString, union_all_func, rasterio, np, rio_features, rio_windows, tree
    )


def _route_buffer_sum(
    route: RouteData,
    index: list[dict[str, Any]],
    buffer_m: float,
    *,
    max_val: float,
    directions: list[Any] | None = None,
    shared: SharedTileIndex | None = None,
) -> tuple[float, float, int]:
    raster = raster_stack()
    shp = shapely_stack()
    np = raster["np"]
    rasterio = raster["rasterio"]
    warp = raster["warp"]
    rio_features = raster["features"]
    rio_windows = raster["windows"]
    LineString = shp["LineString"]
    try:
        from shapely.geometry import Point as ShapelyPoint

        Point = ShapelyPoint
    except Exception:  # noqa: BLE001 — shapely может отсутствовать
        Point = None
    union_all_func = union_all
    dirs = directions if directions is not None else route.directions
    if not dirs or not index:
        return 0.0, 0.0, 0
    stop_coords = _extract_stop_points(dirs)
    bounds, use_stops = _extract_route_bounds(dirs, stop_coords, Point)
    if bounds is None:
        return 0.0, 0.0, 0
    min_lat, max_lat, min_lon, max_lon = bounds
    candidate_tiles, tree = _select_candidates(
        min_lon, min_lat, max_lon, max_lat, index, warp, shared
    )
    crs_set = {tile.get("crs") for tile in candidate_tiles}
    same_crs = len(crs_set) == 1
    target_crs = next(iter(crs_set)) if same_crs else None
    if same_crs and target_crs is not None:
        buffer_poly, corridor_m2 = _unified_result(
            None, 0.0, use_stops, dirs, target_crs, buffer_m, warp, Point, LineString, union_all_func
        )
        if buffer_poly is not None:
            return _unified_route_sum(
                shared, candidate_tiles, tree, buffer_poly, corridor_m2,
                max_val, rasterio, np, rio_features, rio_windows,
            )
    tiles_for_slow = (
        shared.tiles_intersecting_bbox(min_lon, min_lat, max_lon, max_lat)
        if shared is not None
        else candidate_tiles
    )
    return _stops_or_line_sum(
        tiles_for_slow, dirs, buffer_m, max_val, warp, use_stops, Point,
        LineString, union_all_func, np, rasterio, rio_features, rio_windows, tree,
    )


from .buffer import (
    GHS_CACHE_SCHEMA,
    GHS_MAX_OPEN_DATASETS,
    GHS_MAX_VAL,
    GHS_S_MAX_VAL,
    _build_stops_unified_buffer,
    _build_unified_buffer,
    _compute_with_per_tile_buffer,
    _compute_with_per_tile_stops_buffer,
    _compute_with_unified_buffer,
    _extract_stop_points,
)

__all__ = [
    "GHS_CACHE_SCHEMA",
    "GHS_MAX_OPEN_DATASETS",
    "GHS_MAX_VAL",
    "GHS_S_MAX_VAL",
    "SharedTileIndex",
    "_build_stops_unified_buffer",
    "_build_unified_buffer",
    "_close_thread_local_datasets",
    "_compute_with_per_tile_buffer",
    "_compute_with_per_tile_stops_buffer",
    "_compute_with_unified_buffer",
    "_extract_stop_points",
    "_filter_tiles_by_bbox",
    "_get_thread_local_ds",
    "_read_and_mask_tile",
    "_route_buffer_sum",
    "_thread_local",
    "logger",
]
