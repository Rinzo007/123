"""Общие GIS-утилиты: источники файлов и растры."""
import logging
import warnings
from typing import Any

from errors import MissingDependencyError

logger = logging.getLogger("wikiroutes.gis.common")

# rasterize/geometry_mask создаёт in-memory датасет без геотрансформа —
# rasterio шумит NotGeoreferencedWarning, хотя координаты берутся из
# явно переданного transform. Подавляем глобально (основной поток) и
# устойчиво к thread-local фильтрам Python 3.14.
try:
    from rasterio.errors import NotGeoreferencedWarning as _NotGeoreferencedWarning
except ImportError:  # pragma: no cover
    _NotGeoreferencedWarning = None

if _NotGeoreferencedWarning is not None:
    warnings.filterwarnings("ignore", category=_NotGeoreferencedWarning)
# Страховка: подавление по тексту, если класс предупреждения отличается.
warnings.filterwarnings("ignore", message=".*no geotransform.*")

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
    # Устойчиво к thread-local фильтрам Python 3.14: не catch_warnings
    # (который возвращает исходные фильтры и не подавляет в воркерах),
    # а прямая простановка ignore в текущем потоке (по классу и по тексту).
    warnings.simplefilter("ignore", not_georeferenced or Warning)
    warnings.filterwarnings("ignore", message=".*no geotransform.*")
    return features.geometry_mask(
        geometries,
        out_shape=out_shape,
        transform=transform,
        all_touched=all_touched,
        invert=invert,
    )

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
            warnings.simplefilter("ignore", NotGeoreferencedWarning)
        warnings.filterwarnings("ignore", message=".*no geotransform.*")
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
            warnings.simplefilter("ignore", NotGeoreferencedWarning)
        transform = ds.transform
    except (OSError, ValueError, RuntimeError):
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

