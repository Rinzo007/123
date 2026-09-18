"""Построение буферов направлений и потоковая обработка маршрутов Overture."""

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
from .cache import LRUCache, _cache_get, _cache_put, _stats_from_cached
from .context import _OvertureContext
from .geometry import _intersection_union_area, _query_tree
from .settings import (
    OVERTURE_BUFFER_QUAD_SEGS,
    OVERTURE_DIRECTIONS_LATLON,
    OVERTURE_LINE_SIMPLIFY_M,
)

logger = logging.getLogger("wikiroutes.gis.overture")


def _direction_xy(direction: Any) -> np.ndarray | None:
    """Координаты направления в порядке (lon, lat) как numpy-массив."""
    coords = list(getattr(direction, "coords", []))
    if len(coords) < 2:
        return None
    if OVERTURE_DIRECTIONS_LATLON:
        return np.asarray([(float(c[1]), float(c[0])) for c in coords], dtype=float)
    return np.asarray([(float(c[0]), float(c[1])) for c in coords], dtype=float)


def _utm_line_for(xy: np.ndarray, transformer: Any) -> Any | None:
    """Проецирует координаты в UTM и возвращает непустую линию (или None)."""
    tx, ty = transformer.transform(xy[:, 0], xy[:, 1])
    line_utm: Any = LineString(np.column_stack([tx, ty]))
    if line_utm.is_empty or line_utm.length <= 0:
        return None
    return line_utm


def _buffer_utm_line(line_utm: Any, buffer_m: float) -> Any | None:
    """Упрощает линию (при необходимости) и строит буфер направления."""
    if OVERTURE_LINE_SIMPLIFY_M > 0 and line_utm.length > OVERTURE_LINE_SIMPLIFY_M * 2:
        with contextlib.suppress(GEOSException, TypeError, ValueError, RuntimeError):
            line_utm = line_utm.simplify(
                OVERTURE_LINE_SIMPLIFY_M, preserve_topology=True
            )
    buf = line_utm.buffer(buffer_m, quad_segs=OVERTURE_BUFFER_QUAD_SEGS)
    return buf if buf is not None and not buf.is_empty else None


def _overture_direction_buffer(
    direction: Any, transformer: Any, buffer_m: float
) -> Any:
    xy = _direction_xy(direction)
    if xy is None:
        return None
    line_utm = _utm_line_for(xy, transformer)
    if line_utm is None:
        return None
    return _buffer_utm_line(line_utm, buffer_m)


# ---------------------------------------------------------------------------
# Обработка одного маршрута
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class _WorkerState:
    """Потоковое состояние батча, общее для всех обрабатываемых маршрутов."""

    ctx: _OvertureContext
    transformer: Any
    cache: JsonCache | None
    write_cache: bool
    cache_lock: threading.RLock | None
    buffer_cache: LRUCache
    buffer_cache_lock: threading.RLock


def _direction_signatures(rd: RouteData) -> list[str]:
    """Геометрические подписи направлений маршрута (с фолбэком на хеш coords)."""
    dir_sigs: list[str] = []
    for d in rd.directions:
        try:
            sig = str(dir_geo_sig(d))
        except (GEOSException, TypeError, ValueError, AttributeError, RuntimeError):
            coords_repr = repr(list(getattr(d, "coords", [])))
            sig = f"unknown_{hashlib.sha256(coords_repr.encode()).hexdigest()[:16]}"
        dir_sigs.append(sig)
    return dir_sigs


def _build_cached_direction_buffer(
    d: Any, sig: str, key: str, state: _WorkerState
) -> Any | None:
    """Возвращает канонический буфер направления (общий LRU, double-checked lock)."""
    with state.buffer_cache_lock:
        buf = state.buffer_cache.get(sig)
    if buf is not None:
        return buf

    try:
        buf = _overture_direction_buffer(d, state.transformer, state.ctx.buffer_m)
    except (GEOSException, TypeError, ValueError, AttributeError, RuntimeError):
        logger.exception("Overture: ошибка построения буфера для %s", key)
        return None
    if buf is None:
        return None

    with contextlib.suppress(
        GEOSException, TypeError, ValueError, AttributeError, RuntimeError
    ):
        shapely.prepare(buf)
    # Вставляем в кэш под локом с повторной проверкой (double-checked locking):
    # если другой поток уже построил тот же буфер — переиспользуем канонический.
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
    entry = {
        "total_area_m2": st.total_area_m2,
        "corridor_m2": st.corridor_m2,
        "count": st.count,
        "ok": st.ok,
    }
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
            # ИСПРАВЛЕНО (M-02): prepare только для свеже-созданного union.
            # Одиночный буфер — это разделяемый кэшированный объект, он уже
            # подготовлен при вставке в кэш; повторный prepare вызывал гонку.
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
        route_stats = OvertureStats(
            total_area_m2=route_area,
            corridor_m2=float(shapely.area(route_buffer)),
            count=sum(st.count for st in direction_stats),
            ok=route_ok,
        )
        route_entry = {
            "total_area_m2": route_stats.total_area_m2,
            "corridor_m2": route_stats.corridor_m2,
            "count": route_stats.count,
            "ok": route_stats.ok,
        }
        return route_stats, route_entry
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
    route_key = f"{state.ctx.city}_{rd.route_id}_route_{state.ctx.sig}_{route_geo_sig}"

    direction_stats: list[OvertureStats] = []
    dir_stats_map: dict[tuple[int, int], OvertureStats] = {}
    route_buffers: list[Any] = []
    seen_buffer_ids: set[int] = set()
    cache_entries: dict[str, dict[str, Any]] = {}
    route_buffer_missing = False

    for di, d in enumerate(rd.directions):
        key = f"{state.ctx.city}_{rd.route_id}_{di}_{state.ctx.sig}_{dir_sigs[di]}"
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
            _store_cache_entry(state, key, entry, cache_entries)

    if not route_buffers:
        return OvertureStats(ok=False), dir_stats_map, cache_entries

    route_stats, route_entry = _aggregate_route_stats(
        rd, route_buffers, direction_stats, route_buffer_missing, state
    )
    if route_entry is not None:
        _store_cache_entry(state, route_key, route_entry, cache_entries)
    return route_stats, dir_stats_map, cache_entries


def _store_cache_entry(
    state: _WorkerState,
    key: str,
    entry: dict[str, Any],
    cache_entries: dict[str, dict[str, Any]],
) -> None:
    """Кладёт cache-entry в общий кэш (write-режим) или в отложенные записи."""
    if state.write_cache and state.cache is not None:
        _cache_put(state.cache, state.cache_lock, key, entry)
    else:
        cache_entries[key] = entry


# ---------------------------------------------------------------------------
# Батчи для потоковой обработки
# ---------------------------------------------------------------------------
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
    from pyproj import Transformer

    transformer = Transformer.from_crs(
        "EPSG:4326", f"EPSG:{state.ctx.epsg}", always_xy=True
    )
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
