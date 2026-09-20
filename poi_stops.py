"""POI по остановкам: подсчёт точек интереса в радиусе остановок Overture."""
from __future__ import annotations

import hashlib
import logging
import math
import os
from dataclasses import dataclass
from typing import Any

import numpy as np

from .cache import JsonCache
from .compat import stop_lat, stop_lon
from .ghs.buffer import STOP_GEO_MAX_DIST_M, _filter_stops_near_route
from .metrics import PoiStats
from .models import RouteData

logger = logging.getLogger("wikiroutes.gis.poi_stops")

POI_STOPS_CACHE_SCHEMA = 2  # изменена схема из-за улучшения кэша

_PARQUET_COLUMN_SETS = (
    ("geometry", "name", "names"),
    ("geometry", "names"),
    ("geometry",),
)


def _poi_coords(lat: Any, lon: Any) -> tuple[float, float] | None:
    """(lat, lon) как числа в допустимых пределах, или None."""
    try:
        lat_f = float(lat)
        lon_f = float(lon)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(lat_f) and math.isfinite(lon_f)):
        return None
    if not (-90.0 <= lat_f <= 90.0 and -180.0 <= lon_f <= 180.0):
        return None
    return lat_f, lon_f


def _read_poi_parquet(path: str, gpd: Any) -> Any | None:
    """Читает parquet, пробуя наборы колонок; None при неудаче."""
    for columns in _PARQUET_COLUMN_SETS:
        try:
            return gpd.read_parquet(path, columns=list(columns))
        except Exception:  # noqa: BLE001, S112 — пробуем следующий набор колонок
            continue
    try:
        return gpd.read_parquet(path)
    except Exception:  # noqa: BLE001 — битый/несовместимый файл
        return None


def _read_poi_gdf(path: str) -> Any | None:
    """Читает geoparquet/vectortile-файл с POI в GeoDataFrame (или None)."""
    import geopandas as gpd

    if path.lower().endswith((".parquet", ".geoparquet")):
        return _read_poi_parquet(path, gpd)
    try:
        return gpd.read_file(path)
    except Exception:  # noqa: BLE001 — битый/несовместимый файл
        return None


def _ensure_poi_crs(gdf: Any, path: str) -> Any | None:
    """Приводит CRS к EPSG:4326 (или None при ошибке)."""
    if gdf.crs is None:
        return gdf.set_crs("EPSG:4326")
    if str(gdf.crs).upper() != "EPSG:4326":
        try:
            return gdf.to_crs("EPSG:4326")
        except Exception as exc:  # noqa: BLE001 — внешняя CRS-граница
            logger.warning("POI-stops: ошибка CRS %s: %s", path, exc)
            return None
    return gdf


def _filter_poi_points(gdf: Any, path: str) -> Any | None:
    """Оставляет только непустые Point-геометрии (или None при ошибке)."""
    try:
        gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]
        return gdf[gdf.geometry.geom_type == "Point"]
    except Exception as exc:  # noqa: BLE001 — внешняя геометрия
        logger.warning("POI-stops: ошибка фильтрации: %s", exc)
        return None


def _primary_name(raw: Any) -> str:
    if isinstance(raw, dict):
        primary = raw.get("primary")
        if primary is None:
            primary = raw.get("common")
        return str(primary) if primary else ""
    return ""


def _poi_names(gdf: Any) -> np.ndarray:
    if "name" in gdf.columns:
        return gdf["name"].astype(str).fillna("").values
    if "names" in gdf.columns:
        return np.array([_primary_name(raw) for raw in gdf["names"].tolist()], dtype=object)
    # Разрешаем POI без имени – они будут сохранены с пустой строкой
    return np.array([""] * len(gdf), dtype=object)


def _collect_poi_rows(
    lats: np.ndarray, lons: np.ndarray, names: np.ndarray
) -> list[dict[str, Any]]:
    """Собирает POI-точки с валидными координатами."""
    result: list[dict[str, Any]] = []
    for lat, lon, name in zip(lats, lons, names, strict=True):
        coords = _poi_coords(lat, lon)
        if coords is None:
            continue
        result.append({"lat": coords[0], "lon": coords[1], "name": str(name)})
    return result


def _load_poi_geometry(path: str) -> list[dict[str, Any]]:
    """Загружает POI-точки из geoparquet (theme=place с geometry)."""
    gdf = _read_poi_gdf(path)
    if gdf is None or len(gdf) == 0:
        return []
    if "geometry" not in gdf.columns:
        logger.warning("POI-stops: в %s нет geometry", path)
        return []

    gdf = _ensure_poi_crs(gdf, path)
    if gdf is None:
        return []
    gdf = _filter_poi_points(gdf, path)
    if gdf is None or len(gdf) == 0:
        return []

    lats = gdf.geometry.y.values
    lons = gdf.geometry.x.values
    return _collect_poi_rows(lats, lons, _poi_names(gdf))


def _extract_stop_points(
    directions: Any,
) -> list[tuple[float, float]]:
    """Извлекает координаты остановок из направлений."""
    points: list[tuple[float, float]] = []
    for direction in directions:
        stops = getattr(direction, "stops", None)
        if not stops:
            continue
        for stop in stops:
            coords = _poi_coords(stop_lat(stop), stop_lon(stop))
            if coords is not None:
                points.append(coords)
    return points


def _count_poi_near_stops(
    stop_points: list[tuple[float, float]],
    poi_lats: np.ndarray,
    poi_lons: np.ndarray,
    radius_m: float,
) -> int:
    """Считает количество POI в радиусе хотя бы одной остановки."""
    if not stop_points or len(poi_lats) == 0:
        return 0

    radius_deg = radius_m / 111_000.0
    count = 0
    seen: set[int] = set()

    for lat, lon in stop_points:
        candidates = np.where(
            (np.abs(poi_lats - lat) <= radius_deg)
            & (np.abs(poi_lons - lon) <= radius_deg)
        )[0]
        for idx in candidates:
            if idx in seen:
                continue
            dlat = (poi_lats[idx] - lat) * 111_000.0
            cos_lat = max(math.cos(math.radians(lat)), 0.1)
            dlon = (poi_lons[idx] - lon) * 111_000.0 * cos_lat
            dist_m = math.hypot(dlat, dlon)
            if dist_m <= radius_m:
                seen.add(idx)
                count += 1

    return count


def _poi_cache_signature(poi_file: str, buffer_m: float, pois: list[dict[str, Any]]) -> str:
    """Ключ кэша: хеш от содержимого файла (первые 1 МБ) + размер + конфигурация."""
    file_hash = hashlib.md5(usedforsecurity=False)
    try:
        with open(poi_file, "rb") as f:
            file_hash.update(f.read(1024 * 1024))
        file_hash.update(str(os.path.getsize(poi_file)).encode())
    except Exception:  # noqa: BLE001 — файл может исчезнуть между чтениями
        file_hash.update(poi_file.encode())
    content_digest = file_hash.hexdigest()[:16]
    return hashlib.md5(
        f"{poi_file}|{buffer_m:.0f}|schema{POI_STOPS_CACHE_SCHEMA}|{len(pois)}|{content_digest}"
        .encode(),
        usedforsecurity=False,
    ).hexdigest()[:12]


@dataclass(frozen=True, slots=True)
class _PoiCountContext:
    """Общие параметры подсчёта POI для всех направлений."""

    sig: str
    poi_lats: np.ndarray
    poi_lons: np.ndarray
    buffer_m: float
    cache: JsonCache | None


def _direction_poi_count(
    direction: Any,
    cache_key_prefix: str,
    ctx: _PoiCountContext,
) -> int:
    """Количество POI у одного направления (с кэшем)."""
    cache_key = f"{cache_key_prefix}_{ctx.sig}"
    cached = ctx.cache.get("poi_stops", cache_key) if ctx.cache else None
    if cached:
        return cached.get("count", 0)
    stops = _extract_stop_points([direction])
    if stops:
        stops = _filter_stops_near_route([direction], stops, STOP_GEO_MAX_DIST_M)
    count = _count_poi_near_stops(stops, ctx.poi_lats, ctx.poi_lons, ctx.buffer_m)
    if ctx.cache:
        ctx.cache.put("poi_stops", cache_key, {"count": count})
    return count


def compute_poi_stops(
    routes: list[RouteData],
    poi_file: str | None,
    buffer_m: float,
    city: str,
    cache: JsonCache,
) -> tuple[
    dict[int, PoiStats],
    dict[str, Any] | None,
    dict[tuple[int, int], PoiStats],
]:
    """Подсчёт POI в радиусе остановок (по аналогии с GHS).

    Возвращает (route_stats, meta, dir_stats).
    """
    if not poi_file:
        return {}, None, {}
    pois = _load_poi_geometry(poi_file)
    if not pois:
        return {}, None, {}

    poi_lats = np.array([p["lat"] for p in pois], dtype=np.float64)
    poi_lons = np.array([p["lon"] for p in pois], dtype=np.float64)
    ctx = _PoiCountContext(
        sig=_poi_cache_signature(poi_file, buffer_m, pois),
        poi_lats=poi_lats,
        poi_lons=poi_lons,
        buffer_m=buffer_m,
        cache=cache,
    )

    stats: dict[int, PoiStats] = {}
    dir_stats: dict[tuple[int, int], PoiStats] = {}

    for i, route in enumerate(routes, 1):
        if route.error or not route.directions:
            continue

        route_count = 0
        for di, direction in enumerate(route.directions):
            count = _direction_poi_count(
                direction,
                f"poi_stops_{city}_{route.route_id}_{di}",
                ctx,
            )
            dir_stats[(route.route_id, di)] = PoiStats(
                total_value=float(count),
                count=count,
                by_type={},
            )
            route_count += count

        stats[route.route_id] = PoiStats(
            total_value=float(route_count),
            count=route_count,
            by_type={},
        )
        logger.info(
            "POI-stops [%d/%d] %s: %d POI в радиусе %.0f м",
            i, len(routes), route.name, route_count, buffer_m,
        )

    meta = {"buffer_m": buffer_m, "poi_count": len(pois), "file": poi_file}
    return stats, meta, dir_stats


__all__ = ["compute_poi_stops"]