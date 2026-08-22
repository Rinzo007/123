"""Общие GIS-утилиты: источники файлов, растры, UTM и Shapely 2."""

import hashlib
import logging
import math
import warnings
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from shapely.errors import GEOSException

from .errors import MissingDependencyError
from .models import RouteData

logger = logging.getLogger("wikiroutes.gis.common")


def raster_stack() -> dict[str, Any]:
    """Лениво загружает зависимости NumPy/rasterio для растровых расчётов."""
    try:
        import numpy as np
        import rasterio
        from rasterio import features as rio_features
        from rasterio import warp
        from rasterio import windows as rio_windows
    except ImportError as exc:
        raise MissingDependencyError(
            "Для растровых GIS-расчётов нужно установить: pip install numpy rasterio"
        ) from exc
    return {
        "np": np,
        "rasterio": rasterio,
        "warp": warp,
        "features": rio_features,
        "windows": rio_windows,
    }


def geometry_mask_quiet(
    rasterio_module: Any,
    features: Any,
    geometries: Any,
    out_shape: tuple[int, int],
    transform: Any,
    *,
    all_touched: bool = False,
    invert: bool = False,
) -> Any:
    """``features.geometry_mask`` с подавлением NotGeoreferencedWarning.

    rasterize() маскирует через MemoryDataset, который на некоторых
    версиях rasterio эмитирует NotGeoreferencedWarning (read_transform
    до установки геотрансформа). Для нас это шум: transform всегда
    валиден.
    """
    not_georeferenced = getattr(
        getattr(rasterio_module, "errors", None), "NotGeoreferencedWarning", None
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", not_georeferenced or Warning)
        return features.geometry_mask(
            geometries,
            out_shape=out_shape,
            transform=transform,
            all_touched=all_touched,
            invert=invert,
        )


def shapely_stack() -> dict[str, Any]:
    """Лениво загружает Shapely 2 API, используемый GIS-модулями."""
    try:
        import shapely
        from shapely import STRtree
        from shapely.geometry import LineString, mapping, shape
    except ImportError as exc:
        raise MissingDependencyError(
            "Для GIS-расчётов нужно установить: pip install shapely"
        ) from exc
    return {
        "shapely": shapely,
        "LineString": LineString,
        "mapping": mapping,
        "shape": shape,
        "STRtree": STRtree,
    }


def resolve_sources(path: str | None, extensions: Sequence[str]) -> list[str]:
    """Возвращает список файлов по пути, ограничивая его расширениями."""
    if not path:
        return []

    p = Path(path).expanduser().resolve()
    exts = tuple(ext.lower() for ext in extensions)

    if p.is_dir():
        return [
            str(f)
            for f in sorted(p.iterdir())
            if f.is_file() and f.name.lower().endswith(exts)
        ]

    if p.is_file():
        return [str(p)]

    return []


def _open_raster_quiet(path: str, rasterio: Any, *, sharing: bool = True) -> Any:
    """Открывает датасет, подавляя ``NotGeoreferencedWarning``.

    ``rasterio.open`` сам предупреждает о файлах без геопривязки; его
    наличие проверяется отдельно через ``_tile_has_transform``.
    ``sharing=False`` даёт приватный GDAL-дескриптор (для параллельных
    чтений из потоков).
    Возвращает ``None`` при ошибке открытия.
    """
    try:
        from rasterio.errors import NotGeoreferencedWarning
    except ImportError:
        NotGeoreferencedWarning = None

    try:
        if NotGeoreferencedWarning is not None:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", NotGeoreferencedWarning)
                return rasterio.open(path, sharing=sharing)
        return rasterio.open(path, sharing=sharing)
    except (OSError, ValueError, RuntimeError) as exc:
        logger.warning("Пропущен растр %s: %s", path, exc)
        return None


def _tile_has_transform(ds: Any) -> bool:
    """Проверяет наличие осмысленного геотрансформа у датасета.

    ``rasterio`` выдаёт ``NotGeoreferencedWarning`` и возвращает единичную
    матрицу, если у файла нет ни геотрансформа, ни GCP/RPC. Такой тайл
    нельзя геопривязать, и чтение по единичной матрице даёт мусорные
    координаты — его следует пропускать, а не вычислять по пиксельным осям.
    """
    try:
        from rasterio.errors import NotGeoreferencedWarning
    except ImportError:
        NotGeoreferencedWarning = None

    try:
        if NotGeoreferencedWarning is not None:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", NotGeoreferencedWarning)
                transform = ds.transform
        else:
            transform = ds.transform
    except OSError, ValueError, RuntimeError:
        return False

    return transform is not None and not transform.is_identity


def open_raster_index(paths: list[str], rasterio: Any) -> list[dict[str, Any]]:
    """Открывает растровые датасеты и возвращает их метаданные.

    Вызывающий код обязан закрыть датасеты через ``entry["ds"].close()``.
    Тайлы без геопривязки (нет геотрансформа/GCP/RPC) пропускаются с
    предупреждением — их нельзя корректно спроецировать.
    """
    import contextlib

    index: list[dict[str, Any]] = []

    for path in paths:
        ds = _open_raster_quiet(path, rasterio)
        if ds is None:
            continue

        if not _tile_has_transform(ds):
            logger.warning(
                "Пропущен растр без геопривязки (нет геотрансформа/GCP/RPC): %s",
                path,
            )
            with contextlib.suppress(OSError, ValueError, RuntimeError):
                ds.close()
            continue

        index.append(
            {
                "ds": ds,
                "path": path,
                "crs": ds.crs,
                "bounds": ds.bounds,
            }
        )

    return index


def route_geo_sig(route: RouteData) -> str:
    """Строит короткую стабильную сигнатуру геометрии маршрута."""
    points = [
        (round(lat, 4), round(lon, 4))
        for direction in route.directions
        for lat, lon in direction.coords
    ]
    return hashlib.blake2b(
        repr(points).encode("utf-8"),
        digest_size=5,
    ).hexdigest()[:10]


def utm_epsg(lons: Sequence[float], lats: Sequence[float]) -> int:
    """Возвращает EPSG-код подходящей UTM-зоны для набора координат."""
    if len(lons) == 0 or len(lats) == 0:
        raise ValueError("utm_epsg: нужны непустые списки lons/lats")

    mean_lon = sum(lons) / len(lons)
    mean_lat = sum(lats) / len(lats)

    if not (math.isfinite(mean_lon) and math.isfinite(mean_lat)):
        raise ValueError("utm_epsg: координаты содержат NaN/Inf")

    zone = int((mean_lon + 180.0) / 6.0) + 1
    zone = max(1, min(60, zone))
    hemisphere = 32700 if mean_lat < 0 else 32600
    return hemisphere + zone


def union_all(geoms: Sequence[Any], shapely: Any = None) -> Any | None:
    """Объединяет геометрии с fallback для топологических проблем.

    Fallback-операции предназначены только для восстановления проблемных
    геометрий; проект требует Shapely 2 и не поддерживает старые API.
    """
    geoms = list(geoms)
    if not geoms:
        return None

    if shapely is None:
        import shapely

    try:
        return shapely.union_all(geoms)
    except GEOSException, ValueError, TypeError, AttributeError, RuntimeError:
        logger.debug(
            "union_all: прямой вызов не удался, пробуем set_precision",
            exc_info=True,
        )

    try:
        return shapely.union_all([shapely.set_precision(g, 0.01) for g in geoms])
    except GEOSException, ValueError, TypeError, AttributeError, RuntimeError:
        logger.debug(
            "union_all: set_precision не помог, пробуем buffer(0)",
            exc_info=True,
        )

    try:
        cleaned = [g.buffer(0) for g in geoms]
        return shapely.union_all(cleaned)
    except GEOSException, ValueError, TypeError, AttributeError, RuntimeError:
        logger.debug(
            "union_all: buffer(0) не помог, пробуем последовательный union",
            exc_info=True,
        )

    result = geoms[0]
    for geom in geoms[1:]:
        try:
            result = result.union(geom)
        except GEOSException, ValueError, TypeError, AttributeError, RuntimeError:
            try:
                result = result.union(geom.buffer(0))
            except GEOSException, ValueError, TypeError, AttributeError, RuntimeError:
                continue

    return result
