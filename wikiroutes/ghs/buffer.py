"""Буферные вычисления GHS: построение буферов и суммирование значений."""
from __future__ import annotations

import logging
import math
from collections.abc import Callable, Sequence
from typing import Any

from shapely import STRtree
from shapely.geometry import box

logger = logging.getLogger("wikiroutes.gis.ghs")

GHS_MAX_VAL = 100000.0
GHS_S_MAX_VAL = 1e12
GHS_CACHE_SCHEMA = 5
GHS_MAX_OPEN_DATASETS = 64

# Максимальное расстояние (м) от остановки до ближайшего сегмента линии
# маршрута, при котором остановка считается валидной для GHS-буфера.
# В БД встречаются "мусорные" координаты остановок за сотни км от маршрута:
# буфер вокруг них даёт нулевой объём, поэтому такие остановки отбрасываются.
STOP_GEO_MAX_DIST_M = 5000.0


def _project_metric(
    lat: float, lon: float, lat0: float, lon0: float, cos0: float
) -> tuple[float, float]:
    """Плоские метрические координаты (x=восток, y=север) вокруг якоря."""
    return ((lon - lon0) * 111320.0 * cos0, (lat - lat0) * 111110.0)


def _as_finite_lat_lon(lat: Any, lon: Any) -> tuple[float, float] | None:
    try:
        lat_f = float(lat) if lat is not None else None
        lon_f = float(lon) if lon is not None else None
    except (TypeError, ValueError):
        return None
    if lat_f is None or lon_f is None:
        return None
    in_range = math.isfinite(lat_f) and math.isfinite(lon_f)
    in_range = in_range and -90.0 <= lat_f <= 90.0 and -180.0 <= lon_f <= 180.0
    return (lat_f, lon_f) if in_range else None


def _stop_lat_lon(stop: Any) -> tuple[float, float] | None:
    """(lat, lon) остановки или None, если координаты отсутствуют или мусорные."""
    if isinstance(stop, dict):
        lat = stop.get("latitude") if "latitude" in stop else stop.get("lat")
        lon = stop.get("longitude") if "longitude" in stop else stop.get("lon")
    else:
        lat = getattr(stop, "latitude", None)
        if lat is None:
            lat = getattr(stop, "lat", None)
        lon = getattr(stop, "longitude", None)
        if lon is None:
            lon = getattr(stop, "lon", None)
    return _as_finite_lat_lon(lat, lon)


def _direction_coords_pts(coords: Sequence[Any]) -> list[tuple[float, float]]:
    """Кортежи (lat, lon) из координат направления, отбрасывая мусор."""
    pts: list[tuple[float, float]] = []
    for c in coords:
        try:
            la, lo = float(c[0]), float(c[1])
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(la) and math.isfinite(lo)):
            continue
        pts.append((la, lo))
    return pts


def _route_polyline_segments_m(
    directions: Sequence[Any],
) -> tuple[list[tuple[tuple[float, float], tuple[float, float]]], tuple[float, float]]:
    """Сегменты линий маршрутов в локальной метрической проекции.

    Возвращает (сегменты, якорь) — якорь (lat0, lon0) для обратной проекции.
    Проекция эквидистантная вокруг якоря, точна на масштабах до сотен км.
    """
    anchor: tuple[float, float] | None = None
    segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for direction in directions:
        coords = getattr(direction, "coords", None)
        if not coords:
            continue
        pts = _direction_coords_pts(coords)
        if len(pts) < 2:
            continue
        if anchor is None:
            anchor = pts[0]
        lat0, lon0 = anchor
        cos0 = math.cos(math.radians(lat0))
        prev: tuple[float, float] | None = None
        for la, lo in pts:
            m = _project_metric(la, lo, lat0, lon0, cos0)
            if prev is not None:
                segments.append((prev, m))
            prev = m
    if anchor is None:
        anchor = (0.0, 0.0)
    return segments, anchor


def _dist_point_to_segment_m(
    px: float, py: float,
    ax: float, ay: float,
    bx: float, by: float,
) -> float:
    dx = bx - ax
    dy = by - ay
    if dx == 0.0 and dy == 0.0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    cx = ax + t * dx
    cy = ay + t * dy
    return math.hypot(px - cx, py - cy)


def _filter_stops_near_route(
    directions: Sequence[Any],
    stop_points: list[tuple[float, float]],
    max_dist_m: float,
) -> list[tuple[float, float]]:
    if not stop_points or max_dist_m <= 0:
        return stop_points
    segments, anchor = _route_polyline_segments_m(directions)
    if not segments:
        return stop_points
    lat0, lon0 = anchor
    cos0 = math.cos(math.radians(lat0))
    kept: list[tuple[float, float]] = []
    for lat, lon in stop_points:
        px, py = _project_metric(lat, lon, lat0, lon0, cos0)
        best = min(
            _dist_point_to_segment_m(px, py, ax, ay, bx, by)
            for (ax, ay), (bx, by) in segments
        )
        if best <= max_dist_m:
            kept.append((lat, lon))
    return kept


def _extract_stop_points(directions: Sequence[Any]) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for direction in directions:
        stops = getattr(direction, "stops", None)
        if not stops:
            continue
        for stop in stops:
            rc = _stop_lat_lon(stop)
            if rc is not None:
                points.append(rc)
    return _filter_stops_near_route(directions, points, STOP_GEO_MAX_DIST_M)


def _point_buffer(x: float, y: float, buffer_m: float, Point: Any) -> Any | None:
    try:
        pt = Point(float(x), float(y))
        if pt.is_empty:
            return None
        buf = pt.buffer(buffer_m)
        if buf is not None and not buf.is_empty:
            return buf
    except Exception:  # noqa: BLE001 — внешняя геометрия stop
        return None
    return None


def _union_buffers(buffers: list[Any], union_all: Callable) -> Any | None:
    if not buffers:
        return None
    poly = union_all(buffers) if len(buffers) > 1 else buffers[0]
    if poly is None or poly.is_empty:
        return None
    return poly


def _build_stops_unified_buffer(
    directions: Sequence[Any],
    target_crs: Any,
    buffer_m: float,
    warp: Any,
    Point: Any,
    union_all: Callable,
) -> tuple[Any | None, float]:
    stop_coords = _extract_stop_points(directions)
    if not stop_coords:
        return None, 0.0
    try:
        xs, ys = warp.transform(
            "EPSG:4326", target_crs,
            [lon for _, lon in stop_coords],
            [lat for lat, _ in stop_coords],
        )
    except Exception:  # noqa: BLE001 — внешняя проекция
        return None, 0.0
    buffers = [
        buf
        for x, y in zip(xs, ys, strict=True)
        if (buf := _point_buffer(x, y, buffer_m, Point)) is not None
    ]
    poly = _union_buffers(buffers, union_all)
    if poly is None:
        return None, 0.0
    return poly, float(poly.area)


def _directions_to_lines(
    directions: Sequence[Any],
    target_crs: Any,
    warp: Any,
    LineString: Any,
    *,
    buffered: bool = False,
    buffer_m: float = 0.0,
) -> list[Any]:
    """Линии (или их буферы) направлений в системе координат ``target_crs``."""
    lines = []
    for direction in directions:
        if len(direction.coords) < 2:
            continue
        xs, ys = warp.transform(
            "EPSG:4326",
            target_crs,
            [lon for _, lon in direction.coords],
            [lat for lat, _ in direction.coords],
        )
        line = LineString(list(zip(xs, ys, strict=True)))
        if line.is_empty or line.length <= 0:
            continue
        lines.append(line.buffer(buffer_m) if buffered else line)
    return lines


def _build_unified_buffer(
    directions: Sequence[Any],
    target_crs: Any,
    buffer_m: float,
    warp: Any,
    LineString: Any,
    union_all: Callable,
) -> tuple[Any | None, float]:
    poly = _union_buffers(
        _directions_to_lines(directions, target_crs, warp, LineString),
        union_all,
    )
    if poly is None:
        return None, 0.0
    buffer_poly = poly.buffer(buffer_m)
    if buffer_poly is None or buffer_poly.is_empty:
        return None, 0.0
    return buffer_poly, buffer_poly.area


def _compute_with_unified_buffer(
    tiles: list[dict[str, Any]],
    tree: STRtree | None,
    buffer_poly: Any,
    max_val: float,
    rasterio: Any,
    np: Any,
    rio_features: Any,
    rio_windows: Any,
) -> tuple[float, float, int]:
    from .tiles import _read_and_mask_tile
    total_value = 0.0
    tiles_used = 0
    if tree is not None:
        indices = tree.query(buffer_poly, predicate="intersects")
    else:
        indices = range(len(tiles))
    for idx in indices:
        tile = tiles[idx]
        vals = _read_and_mask_tile(tile, buffer_poly, max_val, rasterio, np, rio_features, rio_windows)
        if vals is not None:
            total_value += float(np.sum(vals, dtype=np.float64))
            tiles_used += 1
    return total_value, buffer_poly.area, tiles_used


def _per_tile_line_buffer(
    directions: Sequence[Any],
    ds: Any,
    buffer_m: float,
    warp: Any,
    LineString: Any,
    union_all: Callable,
) -> Any | None:
    """Объединённый буфер линий маршрутов в системе координат растра ds."""
    lines = _directions_to_lines(
        directions, ds.crs, warp, LineString, buffered=True, buffer_m=buffer_m
    )
    return _union_buffers(lines, union_all)


def _per_tile_stops_buffer(
    stop_points: list[tuple[float, float]],
    ds: Any,
    buffer_m: float,
    warp: Any,
    Point: Any,
    union_all: Callable,
) -> Any | None:
    """Объединённый буфер остановок в системе координат растра ds."""
    if not stop_points:
        return None
    xs, ys = warp.transform(
        "EPSG:4326", ds.crs,
        [lon for _, lon in stop_points],
        [lat for lat, _ in stop_points],
    )
    buffers = [
        buf
        for x, y in zip(xs, ys, strict=True)
        if (buf := _point_buffer(x, y, buffer_m, Point)) is not None
    ]
    return _union_buffers(buffers, union_all)


def _read_tile_values(
    tile: dict[str, Any],
    ds: Any,
    tile_buffer: Callable[[Any], Any | None],
    tree: STRtree | None,
    max_val: float,
    rasterio: Any,
    np: Any,
    rio_features: Any,
    rio_windows: Any,
) -> Any:
    from .tiles import _read_and_mask_tile
    poly = tile_buffer(ds)
    if poly is None:
        return None
    if tree is not None:
        b = tile["bounds"]
        tile_box = box(b.left, b.bottom, b.right, b.top)
        if not tile_box.intersects(poly):
            return None
    return _read_and_mask_tile(tile, poly, max_val, rasterio, np, rio_features, rio_windows)


def _compute_with_per_tile_buffers(
    tiles: list[dict[str, Any]],
    tile_buffer: Callable[[Any], Any | None],
    corridor_area: Callable[[Any], float],
    max_val: float,
    rasterio: Any,
    np: Any,
    rio_features: Any,
    rio_windows: Any,
    tree: STRtree | None = None,
) -> tuple[float, float, int]:
    from .tiles import _get_thread_local_ds
    total_value = 0.0
    tiles_used = 0
    area_crs: Any = None
    for tile in tiles:
        ds = _get_thread_local_ds(tile["path"], rasterio)
        if ds is None:
            logger.warning("Пропущен тайл без датасета (ошибка открытия): %s", tile.get("path", "?"))
            continue
        try:
            vals = _read_tile_values(tile, ds, tile_buffer, tree, max_val, rasterio, np, rio_features, rio_windows)
        except Exception as exc:  # noqa: BLE001 — внешняя обработка тайла
            logger.warning("Ошибка обработки тайла %s (тайл пропущен): %s", tile.get("path", "?"), exc)
            continue
        if vals is None:
            continue
        total_value += float(np.sum(vals, dtype=np.float64))
        tiles_used += 1
        if area_crs is None:
            area_crs = ds.crs
    if area_crs is not None:
        corridor_m2 = corridor_area(area_crs)
    else:
        corridor_m2 = 0.0
    return total_value, corridor_m2, tiles_used


def _compute_with_per_tile_buffer(
    tiles: list[dict[str, Any]],
    directions: Sequence[Any],
    buffer_m: float,
    max_val: float,
    warp: Any,
    LineString: Any,
    union_all: Callable,
    rasterio: Any,
    np: Any,
    rio_features: Any,
    rio_windows: Any,
    tree: STRtree | None = None,
) -> tuple[float, float, int]:
    def tile_buffer(ds: Any) -> Any | None:
        return _per_tile_line_buffer(directions, ds, buffer_m, warp, LineString, union_all)

    def corridor_area(crs: Any) -> float:
        _, area = _build_unified_buffer(directions, crs, buffer_m, warp, LineString, union_all)
        return area

    return _compute_with_per_tile_buffers(
        tiles, tile_buffer, corridor_area, max_val, rasterio, np, rio_features, rio_windows, tree,
    )


def _compute_with_per_tile_stops_buffer(
    tiles: list[dict[str, Any]],
    directions: Sequence[Any],
    buffer_m: float,
    max_val: float,
    warp: Any,
    Point: Any,
    union_all: Callable,
    rasterio: Any,
    np: Any,
    rio_features: Any,
    rio_windows: Any,
    tree: STRtree | None = None,
) -> tuple[float, float, int]:
    stop_points = _extract_stop_points(directions)

    def tile_buffer(ds: Any) -> Any | None:
        return _per_tile_stops_buffer(stop_points, ds, buffer_m, warp, Point, union_all)

    def corridor_area(crs: Any) -> float:
        if not stop_points:
            return 0.0
        try:
            _, area = _build_stops_unified_buffer(directions, crs, buffer_m, warp, Point, union_all)
            return area
        except Exception:  # noqa: BLE001 — внешняя проекция остановок
            return 0.0

    return _compute_with_per_tile_buffers(
        tiles, tile_buffer, corridor_area, max_val, rasterio, np, rio_features, rio_windows, tree,
    )
