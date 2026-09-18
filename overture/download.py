"""Автозагрузка тем Overture в локальный кэш (имя кэша, HTTP, атомарная запись)."""

import contextlib
import logging
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .cache import _safe_bbox_key
from .release import OvertureReleaseError, resolve_overture_release
from .http import (
    _download_overture_parts,
    _http_resolve_stac_part_files,
    _part_local_path,
    _read_overture_parts,
)
from .settings import (
    OVERTURE_CACHE_VERSION,
    OVERTURE_THEME_ALIASES,
)

logger = logging.getLogger("wikiroutes.gis.overture")

# Компоненты имени кэш-файла (theme, release) приходят из CLI/env и попадают
# в путь записи/чтения. Whitelist отсекает path traversal («../», «..\»),
# абсолютные пути и служебные символы (в т. ч. NTFS-потоки «file:stream»).
# Точка разрешена: релизы вида «2024-06-13-beta.1» легитимны.
_SAFE_COMPONENT_RE = re.compile(r"^[a-z0-9_.-]+$")


def _safe_cache_component(value: str, *, kind: str) -> str | None:
    """Нормализует и проверяет компонент имени кэш-файла Overture.

    Возвращает нормализованное значение или ``None``, если компонент не
    проходит whitelist (попытка path traversal или мусорный ввод).
    """
    normalized = value.strip().lower()
    if not normalized or not _SAFE_COMPONENT_RE.match(normalized):
        logger.warning(
            "Overture: отклонено недопустимое значение %s=%r "
            "(разрешены [a-z0-9_.-])",
            kind,
            value,
        )
        return None
    return normalized


def _sanitize_package_version(version: str) -> str:
    """Мягко очищает версию пакета (метаданные библиотеки, не ввод пользователя)."""
    cleaned = re.sub(r"[^a-zA-Z0-9_.-]", "_", version.strip())
    return cleaned or "unknown"


def build_overture_cache_name(
    normalized_theme: str,
    package_version: str,
    release_key: str,
    bbox_key: str,
) -> str | None:
    """Собирает безопасное имя кэш-файла из компонентов.

    Возвращает ``None`` с предупреждением, если theme/release не проходят
    whitelist — раньше произвольная строка из CLI/env попадала в путь
    записи/чтения (path traversal, аудит M-5).
    """
    safe_theme = _safe_cache_component(normalized_theme, kind="тема")
    safe_release = _safe_cache_component(release_key, kind="release")
    if safe_theme is None or safe_release is None:
        return None

    safe_package = _sanitize_package_version(package_version)
    return (
        f"overture_v{OVERTURE_CACHE_VERSION}_{safe_theme}_"
        f"{safe_package}_{safe_release}_{bbox_key}"
    )


def _write_geoparquet_atomic(gdf: Any, target: Path) -> None:
    """Атомарно записывает GeoDataFrame в geoparquet (tmp + ``os.replace``).

    Прерванная запись больше не оставляет битый файл, который при следующем
    запуске был бы принят за валидный кэш по факту существования (аудит M-6).
    """
    tmp_file = target.with_name(f"{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        gdf.to_parquet(tmp_file)
        tmp_file.replace(target)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp_file.unlink(missing_ok=True)
        raise


@dataclass(frozen=True, slots=True)
class _AutoDownloadSpec:
    """Параметры подготовленной автозагрузки Overture."""

    theme: str
    bbox: tuple[float, float, float, float]
    effective_release: str | None
    release_key: str
    cache_file: Path


def _prepare_auto_download(
    bbox: tuple[float, float, float, float],
    theme: str,
    release: str | None,
    cache_dir: str | Path,
) -> _AutoDownloadSpec | None:
    """Готовит параметры автозагрузки; None при отсутствии библиотеки/имён-не-whitelist."""
    try:
        import overturemaps
    except ImportError:
        logger.warning(
            "Для автозагрузки Overture: pip install overturemaps pyarrow geopandas"
        )
        return None

    theme_normalized = str(theme).strip().lower()
    normalized_theme = OVERTURE_THEME_ALIASES.get(theme_normalized, theme_normalized)
    if normalized_theme != theme_normalized:
        logger.info("Overture: тема '%s' преобразована в '%s'", theme, normalized_theme)

    min_lat, min_lon, max_lat, max_lon = map(float, bbox)
    bbox_key = _safe_bbox_key((min_lat, min_lon, max_lat, max_lon))
    try:
        effective_release = resolve_overture_release(release)
    except OvertureReleaseError as exc:
        logger.warning("Overture: %s", exc)
        return None
    package_version = getattr(overturemaps, "__version__", "unknown")
    release_key = effective_release

    cache_name = build_overture_cache_name(
        normalized_theme, package_version, release_key, bbox_key
    )
    if cache_name is None:
        # Недопустимая тема/release из CLI/env: отказ до сетевого запроса.
        return None

    cache_file = Path(cache_dir) / "overture_auto" / f"{cache_name}.geoparquet"
    return _AutoDownloadSpec(
        theme=normalized_theme,
        bbox=(min_lat, min_lon, max_lat, max_lon),
        effective_release=effective_release,
        release_key=release_key,
        cache_file=cache_file,
    )


def _http_download_overture_place(
    theme: str,
    bbox: tuple[float, float, float, float],
    release: str | None,
    cache_dir: str | Path,
    retries: int,
    retry_delay: float,
) -> Any:
    """Скачивает тему Overture по HTTP в локальный кэш и читает через pyarrow.

    pyarrow-транспорт S3 в этой среде нестабилен (NETWORK_CONNECTION / TLS EOF),
    поэтому часть данных тянут напрямую по HTTP и читают уже с локального диска.
    """
    import geopandas as gpd

    if theme != "place":
        raise ValueError(f"HTTP-загрузка поддерживает только тему 'place', получено {theme!r}")

    if release is None:
        raise ValueError("effective release должен быть разрешён до HTTP-загрузки")

    keys = _http_resolve_stac_part_files(
        release,
        "places",
        "place",
        bbox,
        retries=retries,
        retry_delay=retry_delay,
    )
    if not keys:
        logger.warning("Overture: STAC не нашёл файлов для bbox %s, release %s", bbox, release)
        return None

    _download_overture_parts(keys, cache_dir, retries, retry_delay)

    local_files = [
        str(_part_local_path(k, cache_dir))
        for k in keys
        if _part_local_path(k, cache_dir).exists()
    ]
    bbox_filter = (
        min(bbox[1], bbox[3]),
        min(bbox[0], bbox[2]),
        max(bbox[1], bbox[3]),
        max(bbox[0], bbox[2]),
    )
    return _read_overture_parts(local_files, bbox_filter, gpd)


def _fetch_and_write_auto(
    spec: _AutoDownloadSpec,
    retries: int,
    retry_delay: float,
) -> str | None:
    """Скачивает тему по HTTP, пишет атомарно в кэш и возвращает путь к файлу."""
    spec.cache_file.parent.mkdir(parents=True, exist_ok=True)
    gdf = _http_download_overture_place(
        spec.theme,
        bbox=spec.bbox,
        release=spec.effective_release,
        cache_dir=spec.cache_file.parent,
        retries=retries,
        retry_delay=retry_delay,
    )
    if gdf is None:
        logger.warning("Overture: не удалось загрузить данные '%s'", spec.theme)
        return None
    if len(gdf) == 0:
        logger.warning("Overture: не найдено объектов в bbox")
        return None

    _write_geoparquet_atomic(gdf, spec.cache_file)
    logger.info("Overture: %d объектов → %s", len(gdf), spec.cache_file)
    return str(spec.cache_file)


def auto_download_overture(
    bbox: tuple[float, float, float, float],
    cache_dir: str | Path,
    theme: str = "place",
    release: str | None = None,
    retries: int = 0,
    retry_delay: float = 2.0,
) -> str | None:
    """Автоматически скачивает тему Overture в локальный кэш (или читает кэш)."""
    spec = _prepare_auto_download(bbox, theme, release, cache_dir)
    if spec is None:
        return None
    retries = max(retries, 0)

    if spec.cache_file.exists():
        logger.info("Overture: кэш найден: %s", spec.cache_file)
        return str(spec.cache_file)

    logger.info(
        "Overture: загрузка '%s' (%.6f, %.6f) — (%.6f, %.6f), release=%s...",
        spec.theme,
        *spec.bbox,
        spec.release_key,
    )

    try:
        return _fetch_and_write_auto(spec, retries, retry_delay)
    except KeyError:
        logger.warning("Overture: неизвестная тема '%s'", theme)
        return None
    except Exception as exc:
        logger.warning("Overture: ошибка автозагрузки: %s", exc)
        logger.exception("Overture auto-download failed")
        return None


def resolve_poi_place_file(
    override: str | None,
    configured: str | None,
    bbox: tuple[float, float, float, float] | None,
    cache_dir: str | Path,
    release: str | None,
    retries: int,
    warn: Callable[[str], None],
) -> str | None:
    """Совместимый прокси к POI-слою."""
    from .poi import resolve_poi_place_file as _resolve_poi_place_file

    return _resolve_poi_place_file(
        override, configured, bbox, cache_dir, release, retries, warn
    )

__all__ = [
    "_SAFE_COMPONENT_RE",
    "_AutoDownloadSpec",
    "_fetch_and_write_auto",
    "_http_download_overture_place",
    "_prepare_auto_download",
    "_safe_cache_component",
    "_sanitize_package_version",
    "_write_geoparquet_atomic",
    "auto_download_overture",
    "build_overture_cache_name",
    "resolve_poi_place_file",
]