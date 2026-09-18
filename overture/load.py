"""Загрузка и подготовка данных Overture из файлов (ремонт, проекция, дедуп).

Сетевой слой (HTTP/S3/STAC, докачка и чтение частей) — в ``overture_http``;
автозагрузка тем в кэш — в ``overture_download``.
"""

import logging
from pathlib import Path
from typing import Any

import numpy as np
import shapely
from shapely.geometry import box as shapely_box

from ..common import resolve_sources, utm_epsg
from .download import resolve_poi_place_file
from .geometry import _geometry_hashes, _repair_polygonal_geometries
from .http import (
    _concat_part_frames,
    _download_overture_parts,
    _http_resolve_stac_part_files,
    _part_local_path,
    _read_part_frames,
)
from .settings import (
    OVERTURE_DEDUPE_BY_GEOMETRY,
    OVERTURE_PROJECTION_CHUNK_SIZE,
    OVERTURE_SKIP_REPAIR,
)

logger = logging.getLogger("wikiroutes.gis.overture")


def load_overture_segments(
    bbox: tuple[float, float, float, float],
    release: str | None,
    cache_dir: str | Path,
    retries: int = 0,
    retry_delay: float = 2.0,
    classes: frozenset[str] | None = None,
) -> Any | None:
    """Скачивает тему ``segment`` (транспортные отрезки дорог) через STAC/HTTP.

    Возвращает ``GeoDataFrame`` с ``LineString``/``MultiLineString``
    геометриями в EPSG:4326 или ``None`` при ошибке/пустом результате.
    """
    import geopandas as gpd

    if release is None:
        try:
            import overturemaps
            release = overturemaps.core.get_latest_release()
        except Exception as exc:  # noqa: BLE001 — внешняя зависимость
            logger.warning("Overture: не удалось определить release: %s", exc)
            return None

    keys = _http_resolve_stac_part_files(
        release,
        "transportation",
        "segment",
        bbox,
        retries=retries,
        retry_delay=retry_delay,
    )
    if not keys:
        logger.warning(
            "Overture: STAC не нашёл segment-файлов для bbox %s, release %s",
            bbox, release,
        )
        return None

    _download_overture_parts(keys, cache_dir, retries, retry_delay)

    local_files = [
        str(_part_local_path(k, cache_dir))
        for k in keys
        if _part_local_path(k, cache_dir).exists()
    ]
    if not local_files:
        return None

    # Фильтр bbox в формате STAC (xmin, ymin, xmax, ymax) = (min_lon, min_lat, max_lon, max_lat)
    min_lat, min_lon, max_lat, max_lon = bbox
    bbox_filter = (
        min(min_lon, max_lon),
        min(min_lat, max_lat),
        max(min_lon, max_lon),
        max(min_lat, max_lat),
    )

    frames = _read_part_frames(local_files, bbox_filter, gpd)
    if not frames:
        return None
    gdf = _concat_part_frames(frames, gpd)

    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]

    # Оставляем только линейные геометрии (LineString / MultiLineString)
    if len(gdf) > 0:
        gdf = gdf[gdf.geometry.geom_type.isin(("LineString", "MultiLineString"))]

    # Дорожная сеть для синтеза строится только по автодорогам: исключаем
    # железные дороги и пешеходные/велодорожки, иначе кратчайшие пути идут
    # по путям и тропинкам.
    gdf = _keep_road_segments(gdf, classes=classes)

    return gdf if len(gdf) > 0 else None


# Классы дорог, по которым строятся автобусные линии (магистрали города).
# В новых релизах Overture колонка ``class`` содержит именно такие значения
# (primary/secondary/tertiary/residential/footway/track/...), а ``subtype`` —
# тип (road/rail/path/water). Мелкие улицы и тропинки исключаются — иначе
# кратчайшие пути залезают в дворы и пешеходные зоны.
_BUS_ROUTE_CLASSES: frozenset[str] = frozenset({
    "primary",
    "secondary",
})


def _keep_road_segments(
    gdf: Any, *, classes: frozenset[str] | None = None
) -> Any:
    """Оставляет только автомобильные дороги, пригодные для автобусных линий.

    Новые релизы Overture: ``subtype`` (road/rail/path/water) + ``class``
    (OSM-класс, включая footway/track/path/steps). Старые релизы: ``class``
    в значениях road/rail/path/transit. Если колонки нет — ``gdf`` без
    изменений.

    Если ``classes`` передан — ``class`` фильтруется по нему, иначе используется
    ``_BUS_ROUTE_CLASSES``.
    """
    if gdf is None or len(gdf) == 0:
        return gdf
    if "class" not in gdf.columns:
        return gdf
    if classes is None:
        classes = _BUS_ROUTE_CLASSES
    if "subtype" in gdf.columns:
        gdf = gdf[gdf["subtype"] == "road"]
        if len(gdf) == 0:
            return gdf
        return gdf[gdf["class"].isin(classes)]
    return gdf[gdf["class"] == "road"]


def overture_resolve_sources(path: str | None) -> list[str]:
    return resolve_sources(path, (".geojson", ".json", ".parquet", ".geoparquet"))


_OVERTURE_PARQUET_COLUMN_SETS = (
    ("geometry", "id", "version"),
    ("geometry", "id"),
    ("geometry",),
)


def _read_parquet_any_columns(path: str, gpd: Any) -> Any:
    for columns in _OVERTURE_PARQUET_COLUMN_SETS:
        try:
            return gpd.read_parquet(path, columns=list(columns))
        except Exception:  # noqa: BLE001, S112 — пробуем следующий набор колонок
            continue
    return gpd.read_parquet(path)


def _read_vector_any_engine(path: str, bbox_geom: Any, gpd: Any) -> Any:
    try:
        return gpd.read_file(path, bbox=bbox_geom, engine="pyogrio")
    except Exception:  # noqa: BLE001, S110 — fallback на движок по умолчанию
        pass
    try:
        return gpd.read_file(path, bbox=bbox_geom)
    except TypeError:
        return gpd.read_file(path)


def _read_overture_file(path: str, bbox_geom: Any, gpd: Any) -> Any:
    try:
        if path.lower().endswith((".parquet", ".geoparquet")):
            return _read_parquet_any_columns(path, gpd)
        return _read_vector_any_engine(path, bbox_geom, gpd)
    except Exception as exc:  # noqa: BLE001 — внешний файл может быть битым/несовместимым
        logger.warning("Overture: пропущен %s: %s", path, exc)
        return None


def _ensure_overture_crs(gdf: Any, path: str) -> Any | None:
    if gdf.crs is None:
        return gdf.set_crs("EPSG:4326")
    if str(gdf.crs).upper() != "EPSG:4326":
        try:
            return gdf.to_crs("EPSG:4326")
        except Exception as exc:  # noqa: BLE001 — внешняя CRS-граница
            logger.warning("Overture: %s — ошибка преобразования CRS: %s", path, exc)
            return None
    return gdf


def _filter_overture_geometry(gdf: Any, path: str) -> Any | None:
    try:
        gdf = gdf[gdf.geometry.notna()]
        gdf = gdf[~gdf.geometry.is_empty]
        return gdf[gdf.geometry.geom_type.isin(("Polygon", "MultiPolygon"))]
    except Exception as exc:  # noqa: BLE001 — внешняя геометрия
        logger.warning("Overture: %s — ошибка проверки геометрии: %s", path, exc)
        return None


def _overture_read_source(path: str, bbox_geom: Any, gpd: Any) -> Any:
    gdf = _read_overture_file(path, bbox_geom, gpd)
    if gdf is None or len(gdf) == 0:
        return None
    if "geometry" not in gdf.columns:
        logger.warning("Overture: в %s отсутствует geometry", path)
        return None

    keep_cols = ["geometry"] + [c for c in ("id", "version") if c in gdf.columns]
    gdf = gdf[keep_cols]

    gdf = _ensure_overture_crs(gdf, path)
    if gdf is None:
        return None
    gdf = _filter_overture_geometry(gdf, path)
    return gdf if gdf is not None and len(gdf) > 0 else None


def _deduplicate_buildings_by_id(gdf: Any) -> Any:
    """Оставляет последнюю версию каждого id здания (если есть id/version)."""
    if "id" not in gdf.columns:
        return gdf
    ids = gdf["id"].astype("string")
    valid_id = ids.notna() & ids.str.len().gt(0)
    if not valid_id.any():
        return gdf

    import pandas as pd

    with_id = gdf[valid_id]
    if "version" in with_id.columns:
        version = pd.to_numeric(with_id["version"], errors="coerce").fillna(-1)
    else:
        version = pd.Series(-1, index=with_id.index, dtype="float64")
    keep_idx = with_id.assign(__version=version).groupby("id", sort=False)["__version"].idxmax()
    new_labels = list(keep_idx.values) + list(gdf.index[~valid_id])
    return gdf.loc[new_labels]


def _deduplicate_buildings(gdf: Any) -> Any:
    before = len(gdf)
    if before == 0:
        return gdf

    gdf = _deduplicate_buildings_by_id(gdf)

    if OVERTURE_DEDUPE_BY_GEOMETRY and len(gdf) > 1:
        hashes = _geometry_hashes(gdf.geometry.values)
        gdf = gdf.copy()
        gdf["__geom_hash"] = hashes
        gdf = gdf.drop_duplicates("__geom_hash", keep="first").drop(
            columns=["__geom_hash"], errors="ignore"
        )

    removed = before - len(gdf)
    if removed:
        logger.info("Overture: удалено дублей зданий: %d", removed)
    return gdf


def _read_bbox_filtered_sources(
    paths: list[str], bbox_geom: Any, gpd: Any
) -> list[Any]:
    """Читает источники и оставляет геометрии, пересекающие bbox."""
    all_gdfs: list[Any] = []
    for p in paths:
        gdf = _overture_read_source(p, bbox_geom, gpd)
        if gdf is None:
            continue
        try:
            gdf = gdf[gdf.geometry.intersects(bbox_geom)]
        except Exception as exc:  # noqa: BLE001 — внешняя геометрия bbox
            logger.warning("Overture: ошибка bbox-фильтра для %s: %s", p, exc)
            continue
        if len(gdf) > 0:
            keep_cols = ["geometry"] + [
                c for c in ("id", "version") if c in gdf.columns
            ]
            all_gdfs.append(gdf[keep_cols])
    return all_gdfs


def _validate_or_repair(geoms: np.ndarray) -> tuple[np.ndarray, int]:
    """Валидация либо ремонт геометрий; возвращает (валидные, число пропущенных)."""
    if OVERTURE_SKIP_REPAIR:
        valid_mask = shapely.is_valid(geoms)
        return geoms[valid_mask], int((~valid_mask).sum())
    return _repair_polygonal_geometries(geoms)


def _project_geometries_to_epsg(
    valid_geometries: np.ndarray, epsg: int, chunk_size: int, gpd: Any
) -> np.ndarray:
    """Проецирует геометрии в UTM-зону; при большом объёме — чанками."""
    if chunk_size <= 0 or len(valid_geometries) <= chunk_size:
        gdf_valid = gpd.GeoSeries(valid_geometries, crs="EPSG:4326")
        gdf_utm = gdf_valid.to_crs(f"EPSG:{epsg}")
        return gdf_utm.geometry.values

    projected_parts: list[np.ndarray] = []
    for start in range(0, len(valid_geometries), chunk_size):
        part = valid_geometries[start : start + chunk_size]
        gs = gpd.GeoSeries(part, crs="EPSG:4326")
        projected_parts.append(gs.to_crs(f"EPSG:{epsg}").values)
        del gs
    return np.concatenate(projected_parts)


def _apply_overture_limit(gdf: Any, limit: int) -> Any:
    """Применяет limit к выборке (может бросить ValueError для отрицательных)."""
    if not limit:
        return gdf
    if limit < 0:
        raise ValueError("limit должен быть >= 0")
    if len(gdf) > limit:
        logger.warning("Overture: применён limit=%d; расчёт неполный", limit)
        return gdf.head(limit)
    return gdf


def load_overture_geometries(
    paths: list[str],
    bbox: tuple[float, float, float, float],
    limit: int = 0,
    epsg: int | None = None,
) -> tuple[np.ndarray, int | None]:
    import geopandas as gpd
    import pandas as pd

    min_lat, min_lon, max_lat, max_lon = map(float, bbox)
    bbox_geom = shapely_box(min_lon, min_lat, max_lon, max_lat)

    all_gdfs = _read_bbox_filtered_sources(paths, bbox_geom, gpd)
    if not all_gdfs:
        return np.asarray([], dtype=object), None

    gdf = gpd.GeoDataFrame(pd.concat(all_gdfs, ignore_index=True), crs="EPSG:4326")
    del all_gdfs

    gdf = _deduplicate_buildings(gdf)
    gdf = _apply_overture_limit(gdf, limit)

    if epsg is None:
        epsg = utm_epsg([min_lon, max_lon], [min_lat, max_lat])
    if epsg is None:
        logger.warning("Overture: не удалось определить UTM-зону, пропуск")
        return np.asarray([], dtype=object), None

    valid_geometries, invalid_count = _validate_or_repair(gdf.geometry.values)
    del gdf

    if invalid_count:
        logger.warning(
            "Overture: пропущено геометрий после repair в 4326: %d", invalid_count
        )
    if len(valid_geometries) == 0:
        return np.asarray([], dtype=object), None

    polygon_geometries = _project_geometries_to_epsg(
        valid_geometries, epsg, OVERTURE_PROJECTION_CHUNK_SIZE, gpd
    )

    return polygon_geometries, epsg


# Перенесено: сетевые/кэш-функции живут в overture_http / overture_download.
# Публичное API сохранено re-export'ом для обратной совместимости импортов.
__all__ = [
    "_BUS_ROUTE_CLASSES",
    "_apply_overture_limit",
    "_deduplicate_buildings",
    "_deduplicate_buildings_by_id",
    "_ensure_overture_crs",
    "_filter_overture_geometry",
    "_keep_road_segments",
    "_overture_read_source",
    "_project_geometries_to_epsg",
    "_read_bbox_filtered_sources",
    "_read_overture_file",
    "_read_parquet_any_columns",
    "_read_vector_any_engine",
    "_validate_or_repair",
    "load_overture_geometries",
    "load_overture_segments",
    "overture_resolve_sources",
    "resolve_poi_place_file",
]