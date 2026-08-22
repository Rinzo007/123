"""Генерация маршрутов и расчёт объёма застройки по растровым данным."""

import contextlib
import logging
import math
import threading
from typing import Any

from .common import (
    _open_raster_quiet,
    _tile_has_transform,
    raster_stack,
    resolve_sources,
)
from .compat import stop_lat, stop_lon, stop_name
from .errors import MissingDependencyError
from .stops import fix_stop_coord
from .support import clean_terminal_name, normalize_terminal

logger = logging.getLogger("wikiroutes.gis.generate")

GEN_K1, GEN_K2, GEN_K3 = 0.156, 0.729, 0.375
GEN_SCALE = 10.4
GEN_MAX_PIXELS = 2048 * 2048
GHS_MAX_VAL = 100000.0

# Интегральные сетки GHS строятся один раз на процесс и переиспользуются
# генерацией маршрутов и расчётом объёмов остановок (ранее — дважды).
_GRIDS_LOCK = threading.Lock()
_GRIDS_CACHE: dict[
    tuple[Any, ...],
    tuple[list[dict[str, Any]], list[dict[str, Any]]],
] = {}


def _stops_area_km2(uniq_stops: dict[str, dict[str, Any]]) -> float:
    """Оценивает площадь охвата уникальных остановок по их координатному bbox."""
    lats: list[float] = []
    lons: list[float] = []

    for record in uniq_stops.values():
        lat = record.get("lat")
        lon = record.get("lon")
        if lat is None or lon is None:
            continue
        try:
            lat_value = float(lat)
            lon_value = float(lon)
        except TypeError, ValueError:
            continue
        if not (math.isfinite(lat_value) and math.isfinite(lon_value)):
            continue
        lats.append(lat_value)
        lons.append(lon_value)

    if len(lats) < 2:
        return 0.0

    lat_span_km = (max(lats) - min(lats)) * 111.0
    lon_span_km = (max(lons) - min(lons)) * 111.0
    lat_center = (min(lats) + max(lats)) / 2.0
    return lat_span_km * lon_span_km * math.cos(math.radians(lat_center))


def gen_route_count_formula(
    pop_thousand: float,
    area_km2: float,
    n_stops: int,
    transfer: float,
) -> int:
    """Оценивает число маршрутов по параметрам города и пересадочности."""
    if pop_thousand < 0 or area_km2 < 0 or n_stops < 0:
        raise ValueError("pop_thousand, area_km2 and n_stops must be non-negative")
    if transfer < 0:
        raise ValueError("transfer must be non-negative")
    raw = GEN_K1 * pop_thousand + GEN_K2 * area_km2 + GEN_K3 * n_stops

    return max(1, round(raw / (GEN_SCALE * max(transfer, 0.1))))


def _build_grids_for_paths(
    paths: list[str],
    bbox_wgs84: tuple[float, float, float, float],
    np: Any,
    rasterio: Any,
    warp: Any,
) -> list[dict[str, Any]]:
    import rasterio.windows as rio_windows
    from rasterio.transform import Affine

    min_lat, min_lon, max_lat, max_lon = bbox_wgs84
    grids: list[dict[str, Any]] = []

    for path in paths:
        ds = None

        try:
            ds = _open_raster_quiet(path, rasterio)
            if ds is None:
                continue

            if not _tile_has_transform(ds):
                logger.warning(
                    "Генерация: пропущен растр без геопривязки "
                    "(нет геотрансформа/GCP/RPC): %s",
                    path,
                )
                with contextlib.suppress(OSError, ValueError, TypeError, RuntimeError):
                    ds.close()
                continue

            xs, ys = warp.transform(
                "EPSG:4326",
                ds.crs,
                [min_lon, max_lon, min_lon, max_lon],
                [min_lat, min_lat, max_lat, max_lat],
            )

            mnx, mxx = min(xs), max(xs)
            mny, mxy = min(ys), max(ys)

            bounds = ds.bounds

            if (
                mxx < bounds.left
                or mnx > bounds.right
                or mxy < bounds.bottom
                or mny > bounds.top
            ):
                continue

            win = rio_windows.from_bounds(mnx, mny, mxx, mxy, ds.transform)
            win = rio_windows.intersection(
                win, rio_windows.Window(0, 0, ds.width, ds.height)
            )

            if win is None or win.width <= 0 or win.height <= 0:
                continue

            out_w, out_h = int(win.width), int(win.height)

            if out_w * out_h > GEN_MAX_PIXELS:
                factor = math.sqrt(GEN_MAX_PIXELS / (out_w * out_h))
                out_w = max(1, int(out_w * factor))
                out_h = max(1, int(out_h * factor))

            arr = ds.read(1, window=win, out_shape=(out_h, out_w)).astype("float64")

            nodata = ds.nodata if ds.nodata is not None else 4294967295.0

            arr[arr == nodata] = 0.0
            arr[~np.isfinite(arr)] = 0.0
            arr[arr < 0] = 0.0
            arr[arr > GHS_MAX_VAL] = 0.0

            arr *= (win.width * win.height) / (out_w * out_h)

            integral = np.zeros((out_h + 1, out_w + 1), dtype="float64")
            integral[1:, 1:] = arr.cumsum(0).cumsum(1)

            wt = rio_windows.transform(win, ds.transform)
            out_transform = wt * Affine.scale(win.width / out_w, win.height / out_h)

            grids.append(
                {
                    "integral": integral,
                    "transform": out_transform,
                    "inv": ~out_transform,
                    "crs": ds.crs,
                    "bounds_m": (mnx, mny, mxx, mxy),
                    "rows": out_h,
                    "cols": out_w,
                    "path": path,
                }
            )

        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            logger.warning("Генерация: тайл %s: %s", path, exc)
        finally:
            if ds is not None:
                with contextlib.suppress(OSError, ValueError, TypeError, RuntimeError):
                    ds.close()

    return grids


def gen_build_volume_grids(
    ghs_path: str | None,
    bbox_wgs84: tuple[float, float, float, float],
    np: Any,
    rasterio: Any,
    warp: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Строит интегральные сетки GHS (кэшируются между вызовами).

    Построение сеток — самая дорогая часть генерации: чтение тайлов и
    кумулятивные суммы. Сетки неизменяемы, поэтому результат для тех же
    путей и bbox переиспользуется. Вторым элементом кортежа возвращается
    пустой список (раньше здесь помещался слой NRES — вычитание нежилой
    застройки удалено).
    """
    paths = tuple(resolve_sources(ghs_path, (".tif", ".tiff")))

    key = (paths, bbox_wgs84)

    with _GRIDS_LOCK:
        cached = _GRIDS_CACHE.get(key)

    if cached is not None:
        return cached

    grids = _build_grids_for_paths(list(paths), bbox_wgs84, np, rasterio, warp)

    with _GRIDS_LOCK:
        _GRIDS_CACHE[key] = (grids, [])

    return grids, []


def gen_square_sum(grid: dict[str, Any], x: float, y: float, half: float) -> float:
    c0f, r0f = grid["inv"] * (x - half, y + half)
    c1f, r1f = grid["inv"] * (x + half, y - half)

    c0 = max(0, min(grid["cols"], math.floor(min(c0f, c1f))))
    c1 = max(0, min(grid["cols"], math.ceil(max(c0f, c1f))))
    r0 = max(0, min(grid["rows"], math.floor(min(r0f, r1f))))
    r1 = max(0, min(grid["rows"], math.ceil(max(r0f, r1f))))

    if c1 <= c0 or r1 <= r0:
        return 0.0

    integral = grid["integral"]

    return float(
        integral[r1, c1] - integral[r0, c1] - integral[r1, c0] + integral[r0, c0]
    )


def gen_point_volume(
    x: float,
    y: float,
    radius_m: float,
    grids: list[dict[str, Any]],
    transformers: list[Any],
) -> float:
    """Оценивает объём застройки в окрестности точки."""
    if radius_m <= 0:
        raise ValueError("radius_m must be positive")
    best = 0.0

    for grid_index, grid in enumerate(grids):
        sx, sy = (
            (x, y)
            if transformers[grid_index] is None
            else transformers[grid_index].transform(x, y)
        )

        b = grid["bounds_m"]

        if (
            b[0] - radius_m <= sx <= b[2] + radius_m
            and b[1] - radius_m <= sy <= b[3] + radius_m
        ):
            best += gen_square_sum(grid, sx, sy, radius_m)
            break

    return max(0.0, best)


def gen_corridor_volume(
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    corridor_m: float,
    grids: list[dict[str, Any]],
    transformers: list[Any],
) -> float:
    """Оценивает объём застройки вдоль коридора."""
    if corridor_m <= 0:
        raise ValueError("corridor_m must be positive")
    dist = math.hypot(x1 - x0, y1 - y0)
    half = max(50.0, corridor_m / 2.0)
    n = max(1, int(dist / corridor_m)) if dist > 1.0 else 1

    total = 0.0

    xs = [x0 + (x1 - x0) * ((i + 0.5) / n) for i in range(n)]
    ys = [y0 + (y1 - y0) * ((i + 0.5) / n) for i in range(n)]

    # Координаты всех точек коридора трансформируются в CRS каждой сетки
    # одним вызовом (pyproj принимает массивы) вместо n отдельных вызовов.
    grid_points: list[tuple[list[float], list[float]]] = []

    for grid_index in range(len(grids)):
        transformer = transformers[grid_index]

        if transformer is None:
            grid_points.append((xs, ys))
        else:
            sx, sy = transformer.transform(xs, ys)
            grid_points.append((list(sx), list(sy)))

    for i in range(n):
        for grid_index, grid in enumerate(grids):
            sx, sy = grid_points[grid_index]
            b = grid["bounds_m"]

            if b[0] - 500 <= sx[i] <= b[2] + 500 and b[1] - 500 <= sy[i] <= b[3] + 500:
                total += gen_square_sum(grid, sx[i], sy[i], half)
                break

    return max(0.0, total)


def compute_stop_volumes(
    uniq_stops: dict[str, dict[str, Any]],
    bbox: tuple[float, float, float, float] | None,
    ghs_path: str | None,
    radius_m: float,
) -> dict[str, float]:
    """Рассчитывает объём застройки вокруг остановок."""
    if radius_m <= 0:
        raise ValueError("radius_m must be positive")
    if not bbox:
        return {}

    try:
        stack = raster_stack()
    except MissingDependencyError:
        logger.exception("Растровые зависимости недоступны")
        return {}

    from pyproj import Transformer

    np = stack["np"]
    rasterio = stack["rasterio"]
    warp = stack["warp"]

    grids, _ = gen_build_volume_grids(
        ghs_path,
        bbox,
        np,
        rasterio,
        warp,
    )

    if not grids:
        logger.warning("Объём остановок: нет тайлов GHS под bbox")
        return {}

    min_lat, min_lon, max_lat, max_lon = bbox

    lat_c = (min_lat + max_lat) / 2
    lon_c = (min_lon + max_lon) / 2

    best = None

    for grid in grids:
        try:
            xs, ys = warp.transform("EPSG:4326", grid["crs"], [lon_c], [lat_c])

            b = grid["bounds_m"]

            if b[0] <= xs[0] <= b[2] and b[1] <= ys[0] <= b[3]:
                best = grid
                break
        except TypeError, ValueError, RuntimeError:
            continue

    if best is None:
        best = grids[0]

    work_crs = best["crs"]

    transformers = [
        None
        if grid is best
        else Transformer.from_crs(work_crs, grid["crs"], always_xy=True)
        for grid in grids
    ]

    to_work = Transformer.from_crs("EPSG:4326", work_crs, always_xy=True)

    volumes: dict[str, float] = {}
    points: list[tuple[str, float, float]] = []

    for key, rec in uniq_stops.items():
        fixed = fix_stop_coord(
            {"latitude": rec["lat"], "longitude": rec["lon"]},
            rec.get("idx", 0),
            bbox,
        )

        if not fixed:
            volumes[key] = 0.0
            continue

        if not (min_lat <= fixed[0] <= max_lat and min_lon <= fixed[1] <= max_lon):
            volumes[key] = 0.0
            continue

        x, y = to_work.transform(fixed[1], fixed[0])
        points.append((key, float(x), float(y)))

    if not points:
        return volumes

    keys = [key for key, _, _ in points]
    xs = [x for _, x, _ in points]
    ys = [y for _, _, y in points]

    # Трансформация всех остановок в CRS каждой сетки одним вызовом
    # (pyproj принимает массивы) вместо отдельного вызова на остановку.
    grid_points: list[tuple[list[float], list[float]]] = []

    for grid_index in range(len(grids)):
        transformer = transformers[grid_index]

        if transformer is None:
            grid_points.append((xs, ys))
        else:
            sx, sy = transformer.transform(xs, ys)
            grid_points.append((list(sx), list(sy)))

    for idx, key in enumerate(keys):
        volume = 0.0

        for grid_index, grid in enumerate(grids):
            sx, sy = grid_points[grid_index]
            b = grid["bounds_m"]

            if (
                b[0] - radius_m <= sx[idx] <= b[2] + radius_m
                and b[1] - radius_m <= sy[idx] <= b[3] + radius_m
            ):
                volume += gen_square_sum(grid, sx[idx], sy[idx], radius_m)
                break

        volumes[key] = max(0.0, volume)

    return volumes


def generate_routes_network(
    uniq_stops: dict[str, dict[str, Any]],
    routes_ok: list[Any],
    ghs_path: str | None,
    bbox_wgs84: tuple[float, float, float, float],
    count: int,
    maxlen_km: float = 20.0,
    corridor_m: float = 400.0,
    r0: float = 500.0,
    rmax: float = 5000.0,
    r_step: float = 200.0,
) -> list[dict[str, Any]]:
    """Генерирует маршруты между терминалами по объёму застройки."""
    if count < 0:
        raise ValueError("count must be non-negative")
    if maxlen_km <= 0 or corridor_m <= 0:
        raise ValueError("maxlen_km and corridor_m must be positive")
    if r0 <= 0 or rmax < r0 or r_step <= 0:
        raise ValueError("require r0 > 0, rmax >= r0 and r_step > 0")
    try:
        stack = raster_stack()
    except MissingDependencyError:
        logger.exception("Растровые зависимости недоступны")
        return []

    from pyproj import Transformer

    np = stack["np"]
    rasterio = stack["rasterio"]
    warp = stack["warp"]

    grids, _ = gen_build_volume_grids(
        ghs_path,
        bbox_wgs84,
        np,
        rasterio,
        warp,
    )

    if not grids:
        logger.warning("Генерация: нет тайлов GHS под bbox сети — пропущено")
        return []

    min_lat, min_lon, max_lat, max_lon = bbox_wgs84

    lat_c = (min_lat + max_lat) / 2
    lon_c = (min_lon + max_lon) / 2

    best = None

    for grid in grids:
        try:
            xs, ys = warp.transform("EPSG:4326", grid["crs"], [lon_c], [lat_c])

            b = grid["bounds_m"]

            if b[0] <= xs[0] <= b[2] and b[1] <= ys[0] <= b[3]:
                best = grid
                break
        except TypeError, ValueError, RuntimeError:
            continue

    if best is None:
        best = grids[0]

    work_crs = best["crs"]

    transformers = [
        None
        if grid is best
        else Transformer.from_crs(work_crs, grid["crs"], always_xy=True)
        for grid in grids
    ]

    to_work = Transformer.from_crs("EPSG:4326", work_crs, always_xy=True)
    from_work = Transformer.from_crs(work_crs, "EPSG:4326", always_xy=True)

    stop_list: list[dict[str, Any]] = []

    for rec in uniq_stops.values():
        fixed = fix_stop_coord(
            {"latitude": rec["lat"], "longitude": rec["lon"]},
            rec.get("idx", 0),
            bbox_wgs84,
        )

        if fixed:
            stop_list.append(
                {
                    "name": rec["name"],
                    "lat": fixed[0],
                    "lon": fixed[1],
                }
            )

    if len(stop_list) < 10:
        logger.warning(
            "Генерация: слишком мало остановок (%d) — пропущено", len(stop_list)
        )
        return []

    s_lats = np.array([stop["lat"] for stop in stop_list])
    s_lons = np.array([stop["lon"] for stop in stop_list])

    sx, sy = to_work.transform(s_lons, s_lats)

    sx = np.asarray(sx, dtype="float64")
    sy = np.asarray(sy, dtype="float64")

    terms: dict[str, dict[str, Any]] = {}

    for route in routes_ok:
        if route.error:
            continue

        for direction in route.directions:
            if not direction.stops:
                continue

            for stop in (direction.stops[0], direction.stops[-1]):
                name = stop_name(stop)
                key = normalize_terminal(clean_terminal_name(name))

                lat = stop_lat(stop)
                lon = stop_lon(stop)

                if key and key not in terms and lat is not None and lon is not None:
                    terms[key] = {
                        "name": clean_terminal_name(name),
                        "lat": float(lat),
                        "lon": float(lon),
                    }

    term_list = list(terms.values())

    if len(term_list) < 2:
        logger.warning("Генерация: меньше 2 конечных — пропущено")
        return []

    tx, ty = to_work.transform(
        [t["lon"] for t in term_list], [t["lat"] for t in term_list]
    )

    for i, terminal in enumerate(term_list):
        terminal["x"] = float(tx[i])
        terminal["y"] = float(ty[i])
        terminal["vol"] = gen_point_volume(
            terminal["x"], terminal["y"], 1000.0, grids, transformers
        )

    term_list.sort(key=lambda terminal: -terminal["vol"])

    logger.info(
        "Конечных: %d, остановок: %d, тайлов GHS: %d",
        len(term_list),
        len(stop_list),
        len(grids),
    )

    span = float(np.hypot(sx.max() - sx.min(), sy.max() - sy.min()))

    generated: list[dict[str, Any]] = []
    used_origins: set[str] = set()
    used_dests: set[str] = set()

    for k in range(count):
        origins = [
            terminal for terminal in term_list if terminal["name"] not in used_origins
        ] or term_list
        origin = origins[0]

        used_origins.add(origin["name"])

        dest_cands = [
            terminal
            for terminal in term_list
            if terminal["name"] != origin["name"]
            and terminal["name"] not in used_dests
            and math.hypot(terminal["x"] - origin["x"], terminal["y"] - origin["y"])
            >= 0.2 * span
        ]

        if not dest_cands:
            dest_cands = [
                terminal for terminal in term_list if terminal["name"] != origin["name"]
            ]

        if not dest_cands:
            break

        dest = dest_cands[0]
        used_dests.add(dest["name"])

        path_idx, path_pts, length_m, total_vol, status = _gen_one_route(
            origin,
            dest,
            sx,
            sy,
            maxlen_km * 1000.0,
            corridor_m,
            r0,
            rmax,
            r_step,
            grids,
            transformers,
        )

        if len(path_idx) < 3:
            logger.warning(
                "Маршрут %d: %s → %s — слишком короткий, пропущен",
                k + 1,
                origin["name"],
                dest["name"],
            )
            continue

        ex, ey = path_pts[-1]

        length_m += math.hypot(dest["x"] - ex, dest["y"] - ey)
        path_pts.append((dest["x"], dest["y"]))

        p_lons, p_lats = from_work.transform(
            [p[0] for p in path_pts], [p[1] for p in path_pts]
        )

        pts = list(zip(p_lats, p_lons, strict=True))

        base_km = haversine_km_local(
            origin["lat"], origin["lon"], dest["lat"], dest["lon"]
        )
        length_km = length_m / 1000.0

        chain = " → ".join(
            [origin["name"]] + [stop_list[i]["name"] for i in path_idx] + [dest["name"]]
        )

        generated.append(
            {
                "n": len(generated) + 1,
                "from": origin["name"],
                "to": dest["name"],
                "length_km": length_km,
                "stops": len(path_idx),
                "curvilinearity": length_km / base_km
                if base_km > 0.05
                else float("inf"),
                "volume": total_vol,
                "status": status,
                "pts": pts,
                "chain": chain,
            }
        )

        logger.info(
            "Сгенерирован [%d/%d] %s → %s: %.1f км, %.0f тыс. м³ (%s)",
            len(generated),
            count,
            origin["name"],
            dest["name"],
            length_km,
            total_vol / 1e3,
            status,
        )

    return generated


def _gen_one_route(
    origin: dict[str, Any],
    dest: dict[str, Any],
    sx: Any,
    sy: Any,
    maxlen_m: float,
    corridor_m: float,
    r0: float,
    rmax: float,
    r_step: float,
    grids: list[dict[str, Any]],
    transformers: list[Any],
) -> tuple[list[int], list[tuple[float, float]], float, float, str]:
    """Строит один маршрут жадным выбором остановок по объёму застройки."""
    if maxlen_m <= 0 or corridor_m <= 0 or r0 <= 0 or rmax < r0 or r_step <= 0:
        raise ValueError("invalid route generation parameters")
    import numpy as np

    used_mask = np.zeros(len(sx), dtype=bool)

    path_idx: list[int] = []
    path_pts: list[tuple[float, float]] = [(origin["x"], origin["y"])]

    px, py = origin["x"], origin["y"]

    length_m = 0.0
    total_vol = 0.0

    status = "нет остановок в радиусе"

    while True:
        if math.hypot(dest["x"] - px, dest["y"] - py) <= max(r0, 400.0):
            status = "достигнута конечная"
            break

        if length_m >= maxlen_m:
            status = "лимит длины"
            break

        d_p = np.hypot(sx - px, sy - py)

        radius = r0
        cand = np.array([], dtype=int)

        while radius <= rmax:
            idx = np.where((d_p <= radius) & (d_p > 30.0))[0]
            idx = idx[~used_mask[idx]]

            if len(idx):
                cand = idx
                break

            radius += r_step

        if not len(cand):
            break

        d_dest = np.hypot(sx[cand] - dest["x"], sy[cand] - dest["y"])
        d_p_dest = math.hypot(px - dest["x"], py - dest["y"])

        fwd = cand[d_dest < d_p_dest - 1.0]

        if not len(fwd):
            fwd = cand

        best_i = None
        best_v = -1.0

        for i in fwd:
            volume = gen_corridor_volume(
                px,
                py,
                float(sx[i]),
                float(sy[i]),
                corridor_m,
                grids,
                transformers,
            )

            if volume > best_v:
                best_v = volume
                best_i = int(i)

        if best_i is None or best_v <= 0:
            status = "коридор без застройки"
            break

        length_m += math.hypot(sx[best_i] - px, sy[best_i] - py)
        total_vol += best_v

        path_idx.append(best_i)

        px, py = float(sx[best_i]), float(sy[best_i])
        path_pts.append((px, py))

        used_mask[best_i] = True

    return path_idx, path_pts, length_m, total_vol, status


def haversine_km_local(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Возвращает расстояние по дуге большого круга в километрах."""

    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)

    a = (
        math.sin(dlat / 2.0) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2.0) ** 2
    )

    return 12742.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
