"""GHS: расчёт объёма застройки (высокоуровневый API)."""
import concurrent.futures
import contextlib
import hashlib
import sys
from collections.abc import Callable
from typing import Any, TypeVar

from ..cache import JsonCache
from ..common import (
    open_raster_index,
    raster_stack,
    resolve_sources,
)
from ..metrics import BuiltSStats, GhsStats
from ..models import RouteData
from ..units import dir_geo_sig
from .tiles import (
    GHS_CACHE_SCHEMA,
    GHS_MAX_OPEN_DATASETS,
    GHS_MAX_VAL,
    GHS_S_MAX_VAL,
    SharedTileIndex,
    _build_unified_buffer,
    _close_thread_local_datasets,
    _compute_with_per_tile_buffer,
    _compute_with_unified_buffer,
    _filter_tiles_by_bbox,
    _get_thread_local_ds,
    _read_and_mask_tile,
    _route_buffer_sum,
    logger,
)

__all__ = [
    "GHS_CACHE_SCHEMA",
    "GHS_MAX_OPEN_DATASETS",
    "GHS_MAX_VAL",
    "GHS_S_MAX_VAL",
    "SharedTileIndex",
    "_build_unified_buffer",
    "_close_thread_local_datasets",
    "_compute_ghs_generic",
    "_compute_with_per_tile_buffer",
    "_compute_with_unified_buffer",
    "_filter_tiles_by_bbox",
    "_get_thread_local_ds",
    "_ghs_cache_sig",
    "_ghs_worker",
    "_read_and_mask_tile",
    "_route_buffer_sum",
    "compute_ghs",
    "compute_ghs_s",
    "logger",
]

T = TypeVar("T", GhsStats, BuiltSStats)

def _stats_from_cache(
    cached: dict[str, Any],
    cache_key: str,
    stats_factory: Callable[[float, float, int], T],
) -> T:
    """Восстанавливает объект статистики из кэш-записи."""
    common = (cached.get("corridor_m2", 0.0), cached.get("tiles_used", 0))
    if cache_key == "ghs":
        return stats_factory(cached.get("volume_m3", 0.0), *common)
    return stats_factory(cached.get("surface_m2", 0.0), *common)


def _store_cached_stats(cache: JsonCache, cache_key: str, key: str, st: T) -> None:
    """Кэширует результат направления (если тайлы реально читались)."""
    if st.tiles_used <= 0:
        return
    value_attr = "volume_m3" if cache_key == "ghs" else "surface_m2"
    cache.put(
        cache_key,
        key,
        {
            value_attr: getattr(st, value_attr),
            "corridor_m2": st.corridor_m2,
            "tiles_used": st.tiles_used,
        },
    )


def _ghs_worker(
    route: RouteData,
    di: int,
    key: str,
    index: list[dict[str, Any]],
    buffer_m: float,
    cache: JsonCache,
    max_val: float,
    cache_key: str,
    stats_factory: Callable[[float, float, int], T],
    shared: SharedTileIndex | None = None,
) -> tuple[int, int, T]:
    """Универсальный воркер для одного направления (GHS или GHS-BUILT-S)."""
    cached = cache.get(cache_key, key)

    if cached:
        st = _stats_from_cache(cached, cache_key, stats_factory)
    else:
        volume_m3, corridor_m2, tiles_used = _route_buffer_sum(
            route,
            index,
            buffer_m,
            max_val=max_val,
            directions=[route.directions[di]],
            shared=shared,
        )
        st = stats_factory(volume_m3, corridor_m2, tiles_used)
        # Результат с tiles_used == 0 — это «буфер построен, но ни одного
        # тайла не прочитано»: транзиентный сбой, а не достоверный ноль.
        # Такой результат НЕ кэшируется, чтобы один сбойный запуск
        # не отравлял кэш нулями на все последующие (диагностика 2026-08).
        _store_cached_stats(cache, cache_key, key, st)

    return route.route_id, di, st

def _ghs_cache_sig(
    paths: list[str],
    cache_key: str,
    buffer_m: float,
    max_val: float,
) -> str:
    """Сигнатура ключей кэша GHS: пути + параметры + версия схемы кэша."""
    payload = (
        ";".join(paths)
        + f"|{cache_key}|{buffer_m:.0f}|{max_val:.0f}"
        + f"|schema{GHS_CACHE_SCHEMA}"
    )
    return hashlib.md5(payload.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]

def _build_tasks(
    routes: list[RouteData],
    city: str,
    sig: str,
) -> list[tuple[RouteData, int, str]]:
    """Задачи (маршрут, индекс направления, ключ кэша) для всех валидных направлений."""
    tasks: list[tuple[RouteData, int, str]] = []
    for route in routes:
        if route.error or not route.directions:
            continue
        for di, direction in enumerate(route.directions):
            key = f"{city}_{route.route_id}_{di}_{sig}_{dir_geo_sig(direction)}"
            tasks.append((route, di, key))
    return tasks


def _run_workers(
    tasks: list[tuple[RouteData, int, str]],
    index: list[dict[str, Any]],
    buffer_m: float,
    cache: JsonCache,
    max_val: float,
    cache_key: str,
    stats_factory: Callable[[float, float, int], T],
    shared_index: SharedTileIndex,
    label: str,
) -> dict[tuple[int, int], T]:
    """Запускает воркеры в пуле и собирает статистику по направлениям."""
    dir_stats: dict[tuple[int, int], T] = {}
    total = len(tasks)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(
                _ghs_worker,
                route,
                di,
                key,
                index,
                buffer_m,
                cache,
                max_val,
                cache_key,
                stats_factory,
                shared_index,
            ): (route, di)
            for route, di, key in tasks
        }

        done = 0
        for future in concurrent.futures.as_completed(futures):
            route, di = futures[future]
            try:
                route_id, _, item = future.result()
            except Exception as exc:  # noqa: BLE001 — изоляция воркеров: ошибка одного направления не роняет батч
                logger.warning(f"{label}: направление {route.name}[{di}]: {exc}")
                continue

            dir_stats[(route_id, di)] = item
            done += 1
            if done % 5 == 0 or done == total:
                sys.stdout.write(f"\r  {label} [{done}/{total}] {route.name}".ljust(130))
                sys.stdout.flush()

    logger.info("")
    return dir_stats


def _aggregate_routes(
    routes: list[RouteData],
    dir_stats: dict[tuple[int, int], T],
    stats_factory: Callable[[float, float, int], T],
    result_attr: str,
) -> dict[int, T]:
    """Сводит статистику направлений в статистику маршрутов."""
    stats: dict[int, T] = {}
    for route in routes:
        if route.error or not route.directions:
            continue
        total_val = 0.0
        total_corridor = 0.0
        total_tiles = 0
        for di in range(len(route.directions)):
            dir_item = dir_stats.get((route.route_id, di))
            if dir_item is not None:
                total_val += getattr(dir_item, result_attr, 0.0)
                total_corridor += dir_item.corridor_m2
                total_tiles += dir_item.tiles_used
        stats[route.route_id] = stats_factory(total_val, total_corridor, total_tiles)
    return stats


def _close_index_datasets(index: list[dict[str, Any]]) -> None:
    """Закрывает временные датасеты индекса (полные пути переоткроются в воркерах)."""
    for tile in index:
        with contextlib.suppress(OSError, ValueError, RuntimeError):
            tile["ds"].close()


def _compute_ghs_generic(
    routes: list[RouteData],
    file_path: str | None,
    buffer_m: float,
    city: str,
    cache: JsonCache,
    max_val: float,
    cache_key: str,
    stats_factory: Callable[[float, float, int], T],
    label: str,
    result_attr: str,  # "volume_m3" для GHS, "surface_m2" для GHS-S
) -> tuple[dict[int, T], dict[str, Any] | None, dict[tuple[int, int], T]]:
    """
    Обобщённая функция для compute_ghs и compute_ghs_s.
    Возвращает (stats, meta, dir_stats).
    """
    try:
        raster_stack()
    except Exception:  # noqa: BLE001 - растровые зависимости могут упасть по-разному
        logger.exception("Растровые зависимости недоступны")
        return {}, None, {}

    paths = resolve_sources(file_path, (".tif", ".tiff"))
    index = open_raster_index(paths, raster_stack()["rasterio"]) if paths else []
    if not index:
        logger.warning(f"{label}: файл не найден — расчёт пропущен.")
        return {}, None, {}

    _close_index_datasets(index)

    sig = _ghs_cache_sig(paths, cache_key, buffer_m, max_val)

    tasks = _build_tasks(routes, city, sig)

    # Индекс тайлов со STRtree строится один раз и разделяется всеми
    # воркерами (запросы к дереву потокобезопасны).
    shared_index = SharedTileIndex(index)

    dir_stats = _run_workers(
        tasks, index, buffer_m, cache, max_val, cache_key, stats_factory,
        shared_index, label,
    )

    stats = _aggregate_routes(routes, dir_stats, stats_factory, result_attr)

    meta = {"buffer_m": buffer_m, "tiles": len(index), "layer": label}
    return stats, meta, dir_stats

def compute_ghs(
    routes: list[RouteData],
    ghs_path: str | None,
    buffer_m: float,
    city: str,
    cache: JsonCache,
) -> tuple[dict[int, GhsStats], dict[str, Any] | None, dict[tuple[int, int], GhsStats]]:
    """GHS объём застройки."""
    return _compute_ghs_generic(
        routes,
        ghs_path,
        buffer_m,
        city,
        cache,
        max_val=GHS_MAX_VAL,
        cache_key="ghs",
        stats_factory=lambda v, c, t: GhsStats(volume_m3=v, corridor_m2=c, tiles_used=t, ok=True),
        label="GHS",
        result_attr="volume_m3"
    )

def compute_ghs_s(
    routes: list[RouteData],
    ghs_s_path: str | None,
    buffer_m: float,
    city: str,
    cache: JsonCache,
    max_val: float = GHS_S_MAX_VAL,
) -> tuple[dict[int, BuiltSStats], dict[str, Any] | None, dict[tuple[int, int], BuiltSStats]]:
    """GHS-BUILT-S площадь застройки."""
    return _compute_ghs_generic(
        routes,
        ghs_s_path,
        buffer_m,
        city,
        cache,
        max_val=max_val,
        cache_key="ghs_s",
        stats_factory=lambda v, c, t: BuiltSStats(surface_m2=v, corridor_m2=c, tiles_used=t, ok=True),
        label="GHS-BUILT-S",
        result_attr="surface_m2"
    )

