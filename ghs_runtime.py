import atexit
import collections
import concurrent.futures
import contextlib
import hashlib
import logging
import sys
import threading
from typing import Any

from .cache import JsonCache
from .common import (
    _open_raster_quiet,
    geometry_mask_quiet,
    open_raster_index,
    raster_stack,
    resolve_sources,
    shapely_stack,
    union_all,
)
from .metrics import BuiltSStats, GhsStats
from .models import RouteData
from .units import dir_geo_sig

logger = logging.getLogger("wikiroutes.gis.ghs")

GHS_MAX_VAL = 100000.0
GHS_S_MAX_VAL = 1e12

_thread_local = threading.local()

# Число одновременно открытых растровых дескрипторов на поток. Чтение идёт
# по окнам, поэтому держать открытыми все тайлы мозаики (десятки тысяч для
# национальных GHS) не нужно — LRU-кэш ограничивает память вне зависимости
# от размера источника.
GHS_MAX_OPEN_DATASETS = 64


def _get_thread_local_ds(path: str, rasterio_module: Any) -> Any:
    """Возвращает приватный DatasetReader для потока (LRU-кэш по пути).

    Совместное чтение одного GDAL-дескриптора из нескольких потоков
    (даже через разные объекты ``rasterio.open`` при ``sharing=True``)
    может падать на Windows с ``Read failed``. Поэтому каждый поток
    открывает файл сам, с ``sharing=False`` — приватный дескриптор,
    который вытесняется из LRU при превышении лимита.
    """
    if not hasattr(_thread_local, "datasets"):
        _thread_local.datasets = collections.OrderedDict()
    cache = _thread_local.datasets
    if path in cache:
        cache.move_to_end(path)
        return cache[path]
    ds = _open_raster_quiet(path, rasterio_module, sharing=False)
    cache[path] = ds
    while len(cache) > GHS_MAX_OPEN_DATASETS:
        _, old_ds = cache.popitem(last=False)
        with contextlib.suppress(Exception):
            old_ds.close()
    return ds


def _close_thread_local_datasets() -> None:
    """Корректно закрывает все открытые в потоках файлы при завершении."""
    if hasattr(_thread_local, "datasets"):
        for ds in _thread_local.datasets.values():
            with contextlib.suppress(Exception):
                ds.close()
        _thread_local.datasets.clear()


atexit.register(_close_thread_local_datasets)


# ═══════════════════════════════════════════════════════════════════════
# УНИВЕРСАЛЬНЫЙ РАСЧЁТ РАСТРА ВНУТРИ БУФЕРА (ОДНО ИЛИ НЕСКОЛЬКО НАПРАВЛЕНИЙ)
# ОПТИМИЗИРОВАННАЯ ВЕРСИЯ:
#   - Единый буфер для всех тайлов (если CRS совпадает)
#   - Пространственный индекс (STRtree) для быстрого отбора тайлов
#   - Площадь коридора вычисляется один раз
#   - geometry_mask вместо rasterize для маскирования
# ═══════════════════════════════════════════════════════════════════════
def _route_buffer_sum(
    route: RouteData,
    index: list[dict[str, Any]],
    buffer_m: float,
    *,
    max_val: float,
    directions: list[Any] | None = None,
) -> tuple[float, float, int]:
    """Сумма значений растра внутри буфера. directions=None — все направления маршрута."""
    raster = raster_stack()
    shp = shapely_stack()

    np = raster["np"]
    rasterio = raster["rasterio"]
    warp = raster["warp"]
    rio_features = raster["features"]
    rio_windows = raster["windows"]

    LineString = shp["LineString"]
    mapping = shp["mapping"]
    shape = shp["shape"]
    from shapely import STRtree
    from shapely.geometry import box

    dirs = directions if directions is not None else route.directions

    coords = [coord for direction in dirs for coord in direction.coords]
    if not coords or not index:
        return 0.0, 0.0, 0

    lats = [lat for lat, _ in coords]
    lons = [lon for _, lon in coords]
    min_lat, max_lat = min(lats), max(lats)
    min_lon, max_lon = min(lons), max(lons)

    # ─── Отбор тайлов, потенциально пересекающих маршрут (по bbox) ───
    candidate_tiles = []
    for tile in index:
        try:
            xs, ys = warp.transform(
                "EPSG:4326",
                tile["crs"],
                [min_lon, max_lon, min_lon, max_lon],
                [min_lat, min_lat, max_lat, max_lat],
            )
            rx0, rx1 = min(xs), max(xs)
            ry0, ry1 = min(ys), max(ys)
            bounds = tile["bounds"]
            if not (
                rx1 < bounds.left
                or rx0 > bounds.right
                or ry1 < bounds.bottom
                or ry0 > bounds.top
            ):
                candidate_tiles.append(tile)
        except Exception:
            candidate_tiles.append(tile)

    if not candidate_tiles:
        candidate_tiles = index

    # ─── Проверка: все ли тайлы имеют одинаковую CRS? ───
    crs_set = {tile.get("crs") for tile in candidate_tiles}
    same_crs = len(crs_set) == 1
    target_crs = next(iter(crs_set)) if same_crs else None

    # ─── Построение единого буфера (если CRS одинаковая) ───
    buffer_poly = None
    corridor_m2 = 0.0
    if same_crs and target_crs is not None:
        # Преобразуем все координаты направлений в целевую CRS
        all_lines = []
        for direction in dirs:
            if len(direction.coords) < 2:
                continue
            xs, ys = warp.transform(
                "EPSG:4326",
                target_crs,
                [lon for _, lon in direction.coords],
                [lat for lat, _ in direction.coords],
            )
            line = LineString(list(zip(xs, ys, strict=True)))
            if not line.is_empty and line.length > 0:
                all_lines.append(line)
        if all_lines:
            poly = (
                union_all(all_lines, shp["shapely"])
                if len(all_lines) > 1
                else all_lines[0]
            )
            if poly is not None and not poly.is_empty:
                buffer_poly = poly.buffer(buffer_m)
                if buffer_poly is not None and not buffer_poly.is_empty:
                    corridor_m2 = buffer_poly.area  # площадь один раз

    # Если CRS разные или построить буфер не удалось, используем старый подход
    use_old_approach = buffer_poly is None or buffer_poly.is_empty

    # ─── Пространственный индекс для тайлов ───
    tile_geoms = []
    tile_list = []
    for tile in candidate_tiles:
        b = tile["bounds"]
        # Создаём полигон границ тайла в его CRS (для индекса)
        geom = box(b.left, b.bottom, b.right, b.top)
        tile_geoms.append(geom)
        tile_list.append(tile)

    tree = STRtree(tile_geoms) if tile_geoms else None

    total_value = 0.0
    tiles_used = 0

    # ─── Основной цикл по тайлам ───
    if not use_old_approach and same_crs:
        # Быстрый путь: единый буфер и один CRS
        # Получаем индексы пересекающихся тайлов
        indices: Any
        if tree is not None:
            query_geom = buffer_poly  # или его bounds для скорости
            # STRtree.query возвращает индексы
            indices = tree.query(query_geom, predicate="intersects")
        else:
            indices = range(len(tile_list))

        for idx in indices:
            tile = tile_list[idx]  # (или tile в цикле)
            path = tile.get("path")
            ds = _get_thread_local_ds(path, rasterio) if path else tile["ds"]
            try:
                # Преобразуем буфер в CRS тайла (если отличается, но по условию same_crs — одинаковый)
                # Но на случай, если мы ошиблись, делаем преобразование
                poly = buffer_poly
                if ds.crs != target_crs:
                    geom_dict = warp.transform_geom(target_crs, ds.crs, mapping(poly))
                    poly = shape(geom_dict)
                if poly is None or poly.is_empty:
                    continue

                minx, miny, maxx, maxy = poly.bounds
                win = rio_windows.from_bounds(
                    minx - 50, miny - 50, maxx + 50, maxy + 50, ds.transform
                )
                win = win.round_lengths().round_offsets()
                full_win = rio_windows.Window(0, 0, ds.width, ds.height)
                win = rasterio.windows.intersection(win, full_win)
                if win is None or win.width <= 0 or win.height <= 0:
                    continue

                arr = ds.read(1, window=win).astype("float64")
                nodata = ds.nodata if ds.nodata is not None else 4294967295.0
                arr[arr == nodata] = np.nan
                arr[~np.isfinite(arr)] = np.nan
                arr[arr < 0] = np.nan
                arr[arr > max_val] = np.nan

                # Маскирование через geometry_mask из rasterio.features.
                win_transform = rio_windows.transform(win, ds.transform)
                mask = geometry_mask_quiet(
                    rasterio,
                    rio_features,
                    [poly],
                    out_shape=(int(win.height), int(win.width)),
                    transform=win_transform,
                    all_touched=False,
                    invert=True,  # True = маска с 1 внутри полигона
                )
                if np.count_nonzero(mask) == 0:
                    continue

                vals = arr[mask]
                vals = vals[np.isfinite(vals)]
                if len(vals) == 0:
                    continue
                vals[vals < 0] = 0.0
                total_value += float(np.sum(vals, dtype=np.float64))
                tiles_used += 1

            except Exception as exc:
                cause = exc.__cause__ or exc.__context__
                detail = f"{type(cause).__name__}: {cause}" if cause else str(exc)
                logger.warning(
                    "Ошибка тайла %s: %s (%s)",
                    tile.get("path", "?"),
                    exc,
                    detail,
                )
                continue
    else:
        # ─── Старый медленный путь (для разнородных CRS или если буфер не построен) ───
        # Для совместимости оставляем исходную логику
        # (код практически идентичен оригинальному, но с возможностью использовать tree для отбора)
        for tile in candidate_tiles:
            tile_path = tile.get("path")
            ds = _get_thread_local_ds(tile_path, rasterio) if tile_path else tile["ds"]
            try:
                # Построение буфера для каждого направления внутри цикла (как было)
                buffers = []
                for direction in dirs:
                    if len(direction.coords) < 2:
                        continue
                    xs, ys = warp.transform(
                        "EPSG:4326",
                        ds.crs,
                        [lon for _, lon in direction.coords],
                        [lat for lat, _ in direction.coords],
                    )
                    line = LineString(list(zip(xs, ys, strict=True)))
                    if not line.is_empty and line.length > 0:
                        buffers.append(line.buffer(buffer_m))
                if not buffers:
                    continue
                poly = (
                    union_all(buffers, shp["shapely"])
                    if len(buffers) > 1
                    else buffers[0]
                )
                if poly is None or poly.is_empty:
                    continue

                # Проверяем пересечение с тайлом через индекс (если есть)
                if tree is not None:
                    b = tile["bounds"]
                    tile_box = box(b.left, b.bottom, b.right, b.top)
                    if not tile_box.intersects(poly):
                        continue

                minx, miny, maxx, maxy = poly.bounds
                win = rio_windows.from_bounds(
                    minx - 50, miny - 50, maxx + 50, maxy + 50, ds.transform
                )
                win = win.round_lengths().round_offsets()
                full_win = rio_windows.Window(0, 0, ds.width, ds.height)
                win = rasterio.windows.intersection(win, full_win)
                if win is None or win.width <= 0 or win.height <= 0:
                    continue

                arr = ds.read(1, window=win).astype("float64")
                nodata = ds.nodata if ds.nodata is not None else 4294967295.0
                arr[arr == nodata] = np.nan
                arr[~np.isfinite(arr)] = np.nan
                arr[arr < 0] = np.nan
                arr[arr > max_val] = np.nan

                win_transform = rio_windows.transform(win, ds.transform)
                mask = geometry_mask_quiet(
                    rasterio,
                    rio_features,
                    [poly],
                    out_shape=(int(win.height), int(win.width)),
                    transform=win_transform,
                    all_touched=False,
                    invert=True,
                )
                if np.count_nonzero(mask) == 0:
                    continue

                vals = arr[mask]
                vals = vals[np.isfinite(vals)]
                if len(vals) == 0:
                    continue
                vals[vals < 0] = 0.0
                total_value += float(np.sum(vals, dtype=np.float64))

                # Площадь суммируем как раньше (если CRS разные)
                area_poly = None
                if target_crs is None:
                    target_crs = ds.crs
                    area_poly = poly
                elif ds.crs == target_crs:
                    area_poly = poly
                else:
                    try:
                        geom_dict = warp.transform_geom(
                            ds.crs, target_crs, mapping(poly)
                        )
                        area_poly = shape(geom_dict)
                    except Exception as exc:
                        logger.warning(
                            "Не удалось репроецировать полигон для площади: %s", exc
                        )
                        area_poly = None
                if area_poly is not None and not area_poly.is_empty:
                    corridor_m2 += float(area_poly.area)
                    tiles_used += 1

            except Exception as exc:
                cause = exc.__cause__ or exc.__context__
                detail = f"{type(cause).__name__}: {cause}" if cause else str(exc)
                logger.warning(
                    "Ошибка тайла %s: %s (%s)",
                    tile.get("path", "?"),
                    exc,
                    detail,
                )
                continue

    # Если по какой-то причине corridor_m2 остался 0, вычисляем его из суммарной площади тайлов (запасной вариант)
    if corridor_m2 == 0.0 and not use_old_approach and buffer_poly is not None:
        corridor_m2 = buffer_poly.area

    return total_value, corridor_m2, tiles_used


def _ghs_dir_worker(
    rd: RouteData,
    di: int,
    key: str,
    index: list[dict[str, Any]],
    buffer_m: float,
    cache: JsonCache,
) -> tuple[int, int, GhsStats]:
    """Считает GHS по одному направлению (выполняется в пуле потоков).

    Растровые чтения (rasterio), маскирование (rasterio.features) и суммы
    numpy отпускают GIL, поэтому потоки дают реальный параллелизм.
    """
    cached = cache.get("ghs", key)

    if cached:
        st = GhsStats(
            volume_m3=cached.get("volume_m3", 0.0),
            corridor_m2=cached.get("corridor_m2", 0.0),
            tiles_used=cached.get("tiles_used", 0),
            ok=True,
        )
    else:
        volume_m3, corridor_m2, tiles_used = _route_buffer_sum(
            rd,
            index,
            buffer_m,
            max_val=GHS_MAX_VAL,
            directions=[rd.directions[di]],
        )
        st = GhsStats(
            volume_m3=volume_m3,
            corridor_m2=corridor_m2,
            tiles_used=tiles_used,
            ok=True,
        )
        cache.put(
            "ghs",
            key,
            {
                "volume_m3": st.volume_m3,
                "corridor_m2": st.corridor_m2,
                "tiles_used": st.tiles_used,
            },
        )

    return rd.route_id, di, st


# ═══════════════════════════════════════════════════════════════════════
# GHS-BUILT-V: ПО КАЖДОМУ НАПРАВЛЕНИЮ ОТДЕЛЬНО
# ═══════════════════════════════════════════════════════════════════════
def compute_ghs(
    routes: list[RouteData],
    ghs_path: str | None,
    buffer_m: float,
    city: str,
    cache: JsonCache,
) -> tuple[dict[int, GhsStats], dict[str, Any] | None, dict[tuple[int, int], GhsStats]]:
    raster = raster_stack()
    rasterio = raster["rasterio"]

    paths = resolve_sources(ghs_path, (".tif", ".tiff"))
    index = open_raster_index(paths, rasterio) if paths else []
    if not index:
        logger.warning("GHS: файл не найден (--ghs-file) — расчёт пропущен.")
        return {}, None, {}
    # Дескрипторы из индекса больше не нужны: чтение идёт через ограниченный
    # LRU-кэш в _get_thread_local_ds, поэтому закрываем их сразу, чтобы не
    # держать открытыми все тайлы мозаики одновременно.
    for tile in index:
        with contextlib.suppress(OSError, ValueError, RuntimeError):
            tile["ds"].close()

    sig = hashlib.md5(
        (";".join(paths) + f"|{buffer_m:.0f}").encode("utf-8")
    ).hexdigest()[:12]

    stats: dict[int, GhsStats] = {}
    dir_stats: dict[tuple[int, int], GhsStats] = {}

    tasks: list[tuple[RouteData, int, str]] = []

    for rd in routes:
        if rd.error or not rd.directions:
            continue

        for di, d in enumerate(rd.directions):
            key = f"{city}_{rd.route_id}_{di}_{sig}_{dir_geo_sig(d)}"
            tasks.append((rd, di, key))

    total = len(tasks)

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(
                _ghs_dir_worker, rd, di, key, index, buffer_m, cache
            ): (
                rd,
                di,
            )
            for rd, di, key in tasks
        }

        done = 0

        for future in concurrent.futures.as_completed(futures):
            rd, di = futures[future]

            try:
                route_id, _, st = future.result()
            except Exception as exc:
                logger.warning("GHS: направление %s[%d]: %s", rd.name, di, exc)
                continue

            dir_stats[(route_id, di)] = st
            done += 1

            if done % 5 == 0 or done == total:
                sys.stdout.write(f"\r  GHS [{done}/{total}] {rd.name}".ljust(120))
                sys.stdout.flush()

    logger.info("")

    for rd in routes:
        if rd.error or not rd.directions:
            continue

        vol = cor = 0.0
        tiles = 0

        for di in range(len(rd.directions)):
            dir_st = dir_stats.get((rd.route_id, di))

            if dir_st is not None:
                vol += dir_st.volume_m3
                cor += dir_st.corridor_m2
                tiles += dir_st.tiles_used

        stats[rd.route_id] = GhsStats(
            volume_m3=vol, corridor_m2=cor, tiles_used=tiles, ok=True
        )

    meta = {"buffer_m": buffer_m, "tiles": len(index)}
    return stats, meta, dir_stats


def _ghs_s_dir_worker(
    route: RouteData,
    di: int,
    key: str,
    index: list[dict[str, Any]],
    buffer_m: float,
    cache: JsonCache,
    max_val: float,
) -> tuple[int, int, BuiltSStats]:
    """Считает GHS-BUILT-S по одному направлению (выполняется в пуле потоков)."""
    cached = cache.get("ghs_s", key)

    if cached:
        item = BuiltSStats(
            surface_m2=float(cached.get("surface_m2", 0.0)),
            corridor_m2=float(cached.get("corridor_m2", 0.0)),
            tiles_used=int(cached.get("tiles_used", 0)),
            ok=True,
        )
    else:
        surface_m2, corridor_m2, tiles_used = _route_buffer_sum(
            route,
            index,
            buffer_m,
            max_val=max_val,
            directions=[route.directions[di]],
        )
        item = BuiltSStats(
            surface_m2=surface_m2,
            corridor_m2=corridor_m2,
            tiles_used=tiles_used,
            ok=True,
        )
        cache.put(
            "ghs_s",
            key,
            {
                "surface_m2": item.surface_m2,
                "corridor_m2": item.corridor_m2,
                "tiles_used": item.tiles_used,
            },
        )

    return route.route_id, di, item


# ═══════════════════════════════════════════════════════════════════════
# GHS-BUILT-S: ПО КАЖДОМУ НАПРАВЛЕНИЮ ОТДЕЛЬНО
# ═══════════════════════════════════════════════════════════════════════
def compute_ghs_s(
    routes: list[RouteData],
    ghs_s_path: str | None,
    buffer_m: float,
    city: str,
    cache: JsonCache,
    max_val: float = GHS_S_MAX_VAL,
) -> tuple[
    dict[int, BuiltSStats], dict[str, Any] | None, dict[tuple[int, int], BuiltSStats]
]:
    """Возвращает (stats, meta, dir_stats): dir_stats[(route_id, di)] = BuiltSStats."""
    try:
        raster_stack()
    except Exception:
        logger.exception("Растровые зависимости недоступны")
        return {}, None, {}

    paths = resolve_sources(ghs_s_path, (".tif", ".tiff"))
    index = open_raster_index(paths, raster_stack()["rasterio"]) if paths else []
    if not index:
        logger.warning("GHS-BUILT-S: файл не найден (--ghs-s-file) — расчёт пропущен.")
        return {}, None, {}
    for tile in index:
        with contextlib.suppress(OSError, ValueError, RuntimeError):
            tile["ds"].close()

    sig = hashlib.md5(
        (";".join(paths) + f"|S|{buffer_m:.0f}|{max_val:.0f}").encode("utf-8")
    ).hexdigest()[:12]

    stats: dict[int, BuiltSStats] = {}
    dir_stats: dict[tuple[int, int], BuiltSStats] = {}

    tasks: list[tuple[RouteData, int, str]] = []

    for route in routes:
        if route.error or not route.directions:
            continue

        for di, d in enumerate(route.directions):
            key = f"{city}_{route.route_id}_{di}_{sig}_{dir_geo_sig(d)}"
            tasks.append((route, di, key))

    total = len(tasks)

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(
                _ghs_s_dir_worker,
                route,
                di,
                key,
                index,
                buffer_m,
                cache,
                max_val,
            ): (route, di)
            for route, di, key in tasks
        }

        done = 0

        for future in concurrent.futures.as_completed(futures):
            route, di = futures[future]

            try:
                route_id, _, item = future.result()
            except Exception as exc:
                logger.warning(
                    "GHS-BUILT-S: направление %s[%d]: %s", route.name, di, exc
                )
                continue

            dir_stats[(route_id, di)] = item
            done += 1

            if done % 5 == 0 or done == total:
                sys.stdout.write(
                    f"\r  GHS-BUILT-S [{done}/{total}] {route.name}".ljust(130)
                )
                sys.stdout.flush()

    logger.info("")

    for route in routes:
        if route.error or not route.directions:
            continue

        total_surface = 0.0
        total_corridor = 0.0
        total_tiles = 0

        for di in range(len(route.directions)):
            dir_item = dir_stats.get((route.route_id, di))

            if dir_item is not None:
                total_surface += dir_item.surface_m2
                total_corridor += dir_item.corridor_m2
                total_tiles += dir_item.tiles_used

        stats[route.route_id] = BuiltSStats(
            surface_m2=total_surface,
            corridor_m2=total_corridor,
            tiles_used=total_tiles,
            ok=True,
        )

    meta = {"buffer_m": buffer_m, "tiles": len(index), "layer": "GHS-BUILT-S"}
    return stats, meta, dir_stats
