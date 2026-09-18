"""Построение буферов направлений и обработка маршрутов Overture."""

import contextlib
import hashlib
import logging
import threading
from dataclasses import dataclass, replace
from typing import Any

import numpy as np
import shapely
from shapely.errors import GEOSException
from shapely.geometry import LineString

from ..cache import JsonCache
from ..metrics import OvertureStats
from ..models import RouteData
from ..units import dir_geo_sig
from .cache import (
    LRUCache,
    _cache_get,
    _cache_put,
    _route_stats_from_cached,
    _stats_from_cached,
    _stats_to_cached,
)
from .context import _OvertureContext

logger = logging.getLogger("wikiroutes.gis.overture")
_TRANSFORMER_LOCAL = threading.local()


def _direction_xy(direction: Any, directions_latlon: bool) -> np.ndarray | None:
    """Координаты направления в порядке (lon, lat) как numpy-массив."""
    coords = list(getattr(direction, "coords", []))
    if len(coords) < 2:
        return None
    if directions_latlon:
        return np.asarray([(float(c[1]), float(c[0])) for c in coords], dtype=float)
    return np.asarray([(float(c[0]), float(c[1])) for c in coords], dtype=float)


def _utm_line_for(xy: np.ndarray, transformer: Any) -> Any | None:
    """Проецирует координаты в UTM и возвращает непустую линию (или None)."""
    tx, ty = transformer.transform(xy[:, 0], xy[:, 1])
    line_utm: Any = LineString(np.column_stack([tx, ty]))
    if line_utm.is_empty or line_utm.length <= 0:
        return None
    return line_utm


def _buffer_utm_line(
    line_utm: Any,
    buffer_m: float,
    line_simplify_m: float,
    buffer_quad_segs: int,
) -> Any | None:
    """Упрощает линию (при необходимости) и строит буфер направления."""
    if line_simplify_m > 0 and line_utm.length > line_simplify_m * 2:
        with contextlib.suppress(GEOSException, TypeError, ValueError, RuntimeError):
            line_utm = line_utm.simplify(
                line_simplify_m, preserve_topology=True
            )
    buf = line_utm.buffer(buffer_m, quad_segs=buffer_quad_segs)
    return buf if buf is not None and not buf.is_empty else None


def _overture_direction_buffer(
    direction: Any,
    transformer: Any,
    buffer_m: float,
    *,
    directions_latlon: bool,
    line_simplify_m: float,
    buffer_quad_segs: int,
) -> Any:
    xy = _direction_xy(direction, directions_latlon)
    if xy is None:
        return None
    line_utm = _utm_line_for(xy, transformer)
    if line_utm is None:
        return None
    return _buffer_utm_line(
        line_utm,
        buffer_m,
        line_simplify_m,
        buffer_quad_segs,
    )


@dataclass(frozen=True, slots=True)
class _WorkerState:
    """Состояние обработки батча, общее для всех маршрутов батча."""

    ctx: _OvertureContext
    transformer: Any
    cache: JsonCache | None
    write_cache: bool
    cache_lock: threading.RLock | None
    buffer_cache: LRUCache
    buffer_cache_lock: threading.RLock


def _get_thread_transformer(epsg: int) -> Any:
    """Кэширует Transformer на рабочий поток вместо создания на каждый батч."""
    current = getattr(_TRANSFORMER_LOCAL, "transformer", None)
    current_epsg = getattr(_TRANSFORMER_LOCAL, "epsg", None)
    if current is None or current_epsg != epsg:
        from pyproj import Transformer

        current = Transformer.from_crs(
            "EPSG:4326", f"EPSG:{epsg}", always_xy=True
        )
        _TRANSFORMER_LOCAL.transformer = current
        _TRANSFORMER_LOCAL.epsg = epsg
    return current


def _direction_signatures(rd: RouteData) -> list[str]:
    """Геометрические подписи направлений маршрута."""
    dir_sigs: list[str] = []
    for d in rd.directions:
        try:
            sig = str(dir_geo_sig(d))
        except (GEOSException, TypeError, ValueError, AttributeError, RuntimeError):
            coords_repr = repr(list(getattr(d, "coords", [])))
            sig = f"unknown_{hashlib.sha256(coords_repr.encode()).hexdigest()[:16]}"
        dir_sigs.append(sig)
    return dir_sigs


def _route_cache_key(ctx: _OvertureContext, route_geo_sig: str) -> str:
    """Ключ результата маршрута не зависит от route_id: важна только геометрия."""
    return f"route_{ctx.sig}_{route_geo_sig}"


def _direction_cache_key(
    ctx: _OvertureContext, direction_geo_sig: str
) -> str:
    """Ключ результата направления не зависит от номера маршрута."""
    return f"direction_{ctx.sig}_{direction_geo_sig}"


def _build_cached_direction_buffer(
    d: Any, sig: str, key: str, state: _WorkerState
) -> Any | None:
    """Возвращает канонический буфер направления из общего LRU."""
    with state.buffer_cache_lock:
        buf = state.buffer_cache.get(sig)
    if buf is not None:
        return buf

    try:
        cfg = state.ctx.config
        buf = _overture_direction_buffer(
            d,
            state.transformer,
            state.ctx.buffer_m,
            directions_latlon=cfg.directions_latlon,
            line_simplify_m=cfg.line_simplify_m,
            buffer_quad_segs=cfg.buffer_quad_segs,
        )
    except (GEOSException, TypeError, ValueError, AttributeError, RuntimeError):
        logger.exception("Overture: ошибка построения буфера для %s", key)
        return None
    if buf is None:
        return None

    with contextlib.suppress(
        GEOSException, TypeError, ValueError, AttributeError, RuntimeError
    ):
        shapely.prepare(buf)

    with state.buffer_cache_lock:
        existing = state.buffer_cache.get(sig)
        if existing is None:
            state.buffer_cache.put(sig, buf)
        else:
            buf = existing
    return buf


def _process_direction(
    buffered: Any | None,
    cached: dict | None,
    state: _WorkerState,
) -> tuple[OvertureStats, dict[str, Any] | None, Any | None]:
    """Статистика одного направления: из кэша или расчёт + cache-entry."""
    if cached is not None:
        st = _stats_from_cached(cached)
        return st, None, buffered
    if buffered is None:
        return OvertureStats(ok=False), None, None

    idxs = _query_tree(buffered, state.ctx)
    area, had_error = _intersection_union_area(buffered, idxs, state.ctx)
    st = OvertureStats(
        total_area_m2=area,
        corridor_m2=float(shapely.area(buffered)),
        count=len(idxs),
        ok=not had_error,
    )
    entry = _stats_to_cached(st)
    return st, entry, buffered


def _aggregate_route_stats(
    rd: RouteData,
    route_buffers: list[Any],
    direction_stats: list[OvertureStats],
    route_buffer_missing: bool,
    state: _WorkerState,
) -> tuple[OvertureStats, dict[str, Any] | None]:
    """Объединяет буферы направлений и считает статистику маршрута."""
    try:
        if len(route_buffers) == 1:
            route_buffer = route_buffers[0]
        else:
            route_buffer = shapely.union_all(route_buffers)
            with contextlib.suppress(
                GEOSException, TypeError, ValueError, AttributeError, RuntimeError
            ):
                shapely.prepare(route_buffer)

        idxs = _query_tree(route_buffer, state.ctx)
        route_area, route_error = _intersection_union_area(
            route_buffer, idxs, state.ctx
        )
        route_ok = (
            not route_error
            and all(st.ok for st in direction_stats)
            and not route_buffer_missing
        )
        # count имеет ту же семантику, что и route_area: уникальные здания
        # внутри объединённого коридора маршрута.
        route_stats = OvertureStats(
            total_area_m2=route_area,
            corridor_m2=float(shapely.area(route_buffer)),
            count=len(idxs),
            ok=route_ok,
        )
        return route_stats, _stats_to_cached(route_stats)
    except (GEOSException, TypeError, ValueError, AttributeError, RuntimeError):
        logger.exception("Overture: ошибка агрегации маршрута %s", rd.route_id)
        return OvertureStats(ok=False), None


def _process_single_route(
    rd: RouteData,
    state: _WorkerState,
) -> tuple[
    OvertureStats, dict[tuple[int, int], OvertureStats], dict[str, dict[str, Any]]
]:
    if rd.error or not rd.directions:
        return OvertureStats(ok=False), {}, {}

    dir_sigs = _direction_signatures(rd)
    route_geo_sig = hashlib.sha256(";".join(dir_sigs).encode()).hexdigest()[:16]
    route_key = _route_cache_key(state.ctx, route_geo_sig)

    # Route-cache теперь полноценный: при попадании не требуется даже строить
    # буферы — ключ определяется только dataset/config + геометрией маршрута.
    cached_route = (
        _cache_get(state.cache, state.cache_lock, route_key)
        if state.cache is not None
        else None
    )
    if cached_route is not None:
        parsed = _route_stats_from_cached(cached_route)
        if parsed is not None:
            route_stats, cached_dirs = parsed
            # Кэш содержит route-id независимо от геометрического ключа; при чтении
            # подставляем фактический id текущего маршрута.
            dir_stats_map = {
                (rd.route_id, di): st
                for (_, di), st in cached_dirs.items()
            }
            return route_stats, dir_stats_map, {}

    direction_stats: list[OvertureStats] = []
    dir_stats_map: dict[tuple[int, int], OvertureStats] = {}
    route_buffers: list[Any] = []
    seen_buffer_ids: set[int] = set()
    cache_entries: dict[str, dict[str, Any]] = {}
    route_buffer_missing = False

    for di, d in enumerate(rd.directions):
        key = _direction_cache_key(state.ctx, dir_sigs[di])
        cached = (
            _cache_get(state.cache, state.cache_lock, key)
            if state.cache is not None
            else None
        )
        buf = _build_cached_direction_buffer(d, dir_sigs[di], key, state)

        st, entry, used_buf = _process_direction(buf, cached, state)
        direction_stats.append(st)
        dir_stats_map[(rd.route_id, di)] = st
        if used_buf is None:
            route_buffer_missing = True
        elif id(used_buf) not in seen_buffer_ids:
            seen_buffer_ids.add(id(used_buf))
            route_buffers.append(used_buf)
        if entry is not None:
            cache_entries[key] = entry

    if not route_buffers:
        return OvertureStats(ok=False), dir_stats_map, cache_entries

    route_stats, route_entry = _aggregate_route_stats(
        rd, route_buffers, direction_stats, route_buffer_missing, state
    )
    if route_entry is not None:
        # route cache хранит обе части результата в одном атомарном payload.
        cache_entries[route_key] = {
            "route": route_entry,
            "directions": {
                f"{rd.route_id}:{di}": _stats_to_cached(st)
                for di, st in enumerate(direction_stats)
            },
        }
    return route_stats, dir_stats_map, cache_entries


def _store_cache_entry(
    state: _WorkerState,
    key: str,
    entry: dict[str, Any],
    cache_entries: dict[str, dict[str, Any]],
) -> None:
    if state.write_cache and state.cache is not None:
        _cache_put(state.cache, state.cache_lock, key, entry)
    else:
        cache_entries[key] = entry


def _chunks(seq: Any, size: int) -> Any:
    if size <= 0:
        yield seq
        return
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def _thread_batch_worker(
    batch: list[RouteData],
    state: _WorkerState,
) -> tuple[
    dict[int, OvertureStats],
    dict[tuple[int, int], OvertureStats],
    dict[str, dict[str, Any]],
]:
    transformer = _get_thread_transformer(state.ctx.epsg)
    state = replace(state, transformer=transformer)

    stats: dict[int, OvertureStats] = {}
    dir_stats_map: dict[tuple[int, int], OvertureStats] = {}
    cache_entries: dict[str, dict[str, Any]] = {}
    for rd in batch:
        try:
            route_stats, dirs, entries = _process_single_route(rd, state)
            stats[rd.route_id] = route_stats
            dir_stats_map.update(dirs)
            cache_entries.update(entries)
        except Exception:
            logger.exception(
                "Overture: ошибка обработки маршрута %s в потоковом батче",
                rd.route_id,
            )
            stats[rd.route_id] = OvertureStats(ok=False)
    return stats, dir_stats_map, cache_entries


def _query_tree(buf: Any, ctx: _OvertureContext) -> np.ndarray:
    return np.asarray(ctx.tree.query(buf, predicate="intersects"), dtype=np.intp)


def _compute_intersections_and_areas(
    geoms: np.ndarray,
    buf: Any,
) -> tuple[np.ndarray, np.ndarray]:
    intersections = shapely.intersection(geoms, buf)
    areas = shapely.area(intersections)
    mask = (areas > 0.0) & (~shapely.is_empty(intersections))
    return intersections[mask], areas[mask]


def _union_all_without_grid(geoms: Any) -> Any:
    try:
        return shapely.union_all(geoms)
    except (GEOSException, TypeError, ValueError, AttributeError, RuntimeError):
        return None


def _safe_union(geoms: Any, ctx: _OvertureContext) -> Any:
    try:
        if ctx.config.union_grid_size is not None:
            return shapely.union_all(geoms, grid_size=ctx.config.union_grid_size)
        return shapely.union_all(geoms)
    except (GEOSException, TypeError, ValueError, AttributeError, RuntimeError):
        return _union_all_without_grid(geoms)


def _union_intersections(
    valid_intersections: np.ndarray,
    ctx: _OvertureContext,
) -> Any | None:
    if valid_intersections.size == 0:
        return None
    if valid_intersections.size == 1:
        return valid_intersections[0]

    if ctx.config.use_coverage_union and hasattr(shapely, "coverage_union_all"):
        try:
            merged = shapely.coverage_union_all(valid_intersections)
            if merged is not None and not shapely.is_empty(merged):
                return merged
        except (GEOSException, TypeError, ValueError, AttributeError, RuntimeError):
            pass

    if valid_intersections.size > 5000:
        chunk_size = 5000
        partials = [
            _safe_union(valid_intersections[i : i + chunk_size], ctx)
            for i in range(0, valid_intersections.size, chunk_size)
        ]
        merged = _safe_union(partials, ctx)
    else:
        merged = _safe_union(valid_intersections, ctx)

    if merged is not None and not shapely.is_empty(merged):
        return merged
    return None


def _intersection_union_area_loop(
    buf: Any,
    candidate_indices: np.ndarray,
    ctx: _OvertureContext,
) -> tuple[float, bool]:
    idxs = np.asarray(candidate_indices, dtype=np.intp)
    if idxs.size == 0:
        return 0.0, False

    geoms = ctx.polygon_geometries[idxs]
    intersections_list: list[Any] = []
    had_error = False

    for position, idx in enumerate(idxs):
        try:
            inter = shapely.intersection(geoms[position], buf)
            if inter is not None and not shapely.is_empty(inter) and shapely.area(inter) > 0:
                intersections_list.append(inter)
        except (GEOSException, TypeError, ValueError, AttributeError, RuntimeError) as exc:
            had_error = True
            logger.warning(
                "Overture: ошибка intersection для здания %d: %s", int(idx), exc
            )

    if not intersections_list:
        return 0.0, had_error

    try:
        merged = shapely.union_all(intersections_list)
        if merged is not None and not shapely.is_empty(merged):
            return float(shapely.area(merged)), had_error
    except (GEOSException, TypeError, ValueError, AttributeError, RuntimeError):
        logger.exception("Overture: не удалось объединить пересечения зданий")
    return 0.0, True


def _intersection_union_area(
    buf: Any,
    candidate_indices: np.ndarray,
    ctx: _OvertureContext,
) -> tuple[float, bool]:
    idxs = np.asarray(candidate_indices, dtype=np.intp)
    if idxs.size == 0:
        return 0.0, False

    try:
        candidates = ctx.polygon_geometries[idxs]
        valid_intersections, areas = _compute_intersections_and_areas(candidates, buf)
        if valid_intersections.size == 0:
            return 0.0, False
        if ctx.config.assume_no_overlap:
            return float(areas.sum()), False
        if valid_intersections.size == 1:
            return float(areas[0]), False
        merged = _union_intersections(valid_intersections, ctx)
        if merged is None or shapely.is_empty(merged):
            return 0.0, False
        return float(shapely.area(merged)), False
    except (GEOSException, TypeError, ValueError, AttributeError, RuntimeError):
        logger.exception("Overture: vectorized intersection failed, fallback to loop")
        return _intersection_union_area_loop(buf, idxs, ctx)
