"""Дедупликация маршрутов: анализ перекрытий и выбор «жертв».
Работает в связке с .common (union_all / utm_epsg).
Пакет wikiroutes.gis требует Shapely >= 2, поэтому Shapely-1 fallback’и удалены.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterator
from typing import Any, TypeAlias

import numpy as np
import pandas as pd
from pyproj.exceptions import CRSError, ProjError
from shapely.errors import GEOSException

from .common import union_all, utm_epsg
from .errors import MissingDependencyError
from .support import type_label
from .type_defs import DirectionKey

logger = logging.getLogger("wikiroutes.gis.dedup")

DedupId: TypeAlias = int | DirectionKey


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Безопасно приводит значение к float; NaN/inf заменяет на default."""
    try:
        result = float(value)
    except TypeError, ValueError:
        return default
    if not np.isfinite(result):
        return default
    return result


def _finite_positive_max(series: pd.Series) -> float:
    """Возвращает положительный максимум серии; при отсутствии/NaN возвращает 1.0."""
    if len(series) == 0:
        return 1.0
    value = _safe_float(series.max(), default=1.0)
    if value <= 0.0:
        return 1.0
    return value


def _type_label(value: Any) -> str:
    """Безопасно получает человекочитаемый тип маршрута."""
    try:
        return type_label(value)
    except TypeError, AttributeError:
        return ""


def _dedup_stack() -> dict[str, Any]:
    """Импорт современных зависимостей для дедупликации."""
    try:
        import shapely
        from pyproj import Transformer
        from shapely import STRtree
        from shapely.geometry import LineString
    except ImportError as exc:
        raise MissingDependencyError(
            "Для --dedup нужно установить: pip install numpy pandas pyproj shapely"
        ) from exc
    return {
        "shapely": shapely,
        "Transformer": Transformer,
        "LineString": LineString,
        "STRtree": STRtree,
    }


def _line_parts(geom: Any) -> Iterator[Any]:
    """Возвращает только линейные части геометрии."""
    if geom is None:
        return
    try:
        if geom.is_empty:
            return
    except AttributeError, TypeError:
        return

    geom_type = getattr(geom, "geom_type", "")
    if geom_type == "LineString":
        yield geom
    elif geom_type == "MultiLineString":
        try:
            yield from geom.geoms
        except AttributeError, TypeError:
            return
    elif geom_type == "GeometryCollection":
        try:
            for part in geom.geoms:
                yield from _line_parts(part)
        except AttributeError, TypeError:
            return


def _query_indices(tree: Any, geom: Any, predicate: str = "intersects") -> list[int]:
    """Возвращает локальные integer indices из Shapely 2 STRtree."""
    if geom is None:
        return []
    try:
        if geom.is_empty:
            return []
    except AttributeError, TypeError:
        return []

    try:
        raw = tree.query(geom, predicate=predicate)
    except (GEOSException, TypeError, ValueError, AttributeError) as exc:
        logger.debug("STRtree.query failed: %s", exc)
        return []

    if raw is None:
        return []
    try:
        raw_iter = iter(raw)
    except TypeError:
        raw_iter = iter((raw,))

    out: list[int] = []
    for item in raw_iter:
        try:
            idx = int(item)
        except TypeError, ValueError, OverflowError:
            continue
        if idx >= 0:
            out.append(idx)
    return out


def _bounds_overlap(
    bounds_a: tuple[float, float, float, float],
    bounds_b: tuple[float, float, float, float],
) -> bool:
    """Пересекаются ли прямоугольники (bbox) двух геометрий."""
    minx_a, miny_a, maxx_a, maxy_a = bounds_a
    minx_b, miny_b, maxx_b, maxy_b = bounds_b
    return not (
        maxx_a < minx_b or maxx_b < minx_a or maxy_a < miny_b or maxy_b < miny_a
    )


def _covered_length_loop(lines: Any, other_buffer: Any) -> float:
    """Поточечная реализация (fallback при ошибке батч-пересечения)."""
    total = 0.0
    for line in lines:
        if line is None or getattr(line, "is_empty", True):
            continue
        try:
            intersection = line.intersection(other_buffer)
            for part in _line_parts(intersection):
                try:
                    length = float(part.length)
                except TypeError, ValueError, AttributeError:
                    continue
                if np.isfinite(length):
                    total += length
        except GEOSException, TypeError, ValueError, AttributeError, RuntimeError:
            continue
    return total


def _covered_length(lines: Any, other_buffer: Any, shapely_module: Any) -> float:
    """Суммарная длина частей линий, попавших в буфер другого маршрута."""
    try:
        if lines is None or other_buffer is None:
            return 0.0
        if getattr(other_buffer, "is_empty", True):
            return 0.0

        buffer_bounds = other_buffer.bounds
        keep: list[Any] = []
        for line in lines:
            if line is None or getattr(line, "is_empty", True):
                continue
            if not _bounds_overlap(line.bounds, buffer_bounds):
                continue
            keep.append(line)

        if not keep:
            return 0.0

        if len(keep) == 1:
            intersection = keep[0].intersection(other_buffer)
        else:
            try:
                intersection = shapely_module.intersection(
                    np.asarray(keep, dtype=object),
                    other_buffer,
                )
            except GEOSException, TypeError, ValueError, RuntimeError:
                return _covered_length_loop(keep, other_buffer)

        total = 0.0
        for geom in np.atleast_1d(intersection):
            try:
                length = float(geom.length)
            except TypeError, ValueError, AttributeError:
                continue
            if np.isfinite(length):
                total += length
    except (GEOSException, TypeError, ValueError, AttributeError, RuntimeError) as exc:
        logger.warning("Intersection failed in _covered_length for %s", type(exc).__name__)
        return 0.0
    else:
        return total


def _dedupe_xy_loop(
    points: list[tuple[float, float]],
    eps: float,
) -> list[tuple[float, float]]:
    """Поточечная реализация (fallback для нерегулярных данных)."""
    out: list[tuple[float, float]] = []
    last_grid: tuple[float, float] | None = None
    for x, y in points:
        if not (np.isfinite(x) and np.isfinite(y)):
            continue
        try:
            gx = round(x / eps) * eps
            gy = round(y / eps) * eps
        except TypeError, ValueError, OverflowError:
            continue
        grid_key = (gx, gy)
        if last_grid != grid_key:
            out.append((x, y))
            last_grid = grid_key
    return out


def _dedupe_xy(
    points: list[tuple[float, float]],
    eps: float = 0.01,
) -> list[tuple[float, float]]:
    """Удаляет последовательные дубли точек после округления до сетки."""
    if eps <= 0.0:
        return list(points)
    try:
        arr = np.asarray(points, dtype=np.float64)
    except TypeError, ValueError, OverflowError:
        arr = None
    if arr is None or arr.ndim != 2 or arr.shape[1] < 2 or arr.shape[0] == 0:
        return _dedupe_xy_loop(points, eps)
    finite = np.isfinite(arr).all(axis=1)
    if not finite.any():
        return []
    arr = arr[finite]
    grid = np.round(arr / eps) * eps
    keep = np.empty(arr.shape[0], dtype=bool)
    keep[0] = True
    keep[1:] = (grid[1:] != grid[:-1]).any(axis=1)
    return [(float(x), float(y)) for x, y in arr[keep]]


def _project_routes(
    routes_geo: dict[DedupId, list[list[tuple[float, float]]]],
    transformer: Any,
    np_module: Any,
    LineString: Any,
) -> dict[DedupId, list[Any]]:
    """Проецирует координаты маршрутов в UTM и строит LineString."""
    lines: dict[DedupId, list[Any]] = {}
    for route_id, variants in routes_geo.items():
        route_lines: list[Any] = []
        for variant in variants:
            if len(variant) < 2:
                continue
            try:
                arr = np_module.asarray(variant, dtype=np.float64)
            except TypeError, ValueError:
                logger.debug("Cannot convert variant to numpy array")
                continue
            if arr.ndim != 2 or arr.shape[0] < 2 or arr.shape[1] < 2:
                continue
            try:
                xs, ys = transformer.transform(arr[:, 0], arr[:, 1])
            except CRSError, ProjError, TypeError, ValueError, RuntimeError:
                logger.debug("Transformer failed")
                continue
            xs = np_module.asarray(xs, dtype=np.float64)
            ys = np_module.asarray(ys, dtype=np.float64)
            mask = np_module.isfinite(xs) & np_module.isfinite(ys)
            if not mask.any():
                continue
            xs = xs[mask]
            ys = ys[mask]
            pts = _dedupe_xy(list(zip(xs.tolist(), ys.tolist(), strict=True)))
            if len(pts) < 2:
                pts = list(zip(xs.tolist(), ys.tolist(), strict=True))
            if len(pts) >= 2:
                try:
                    route_lines.append(LineString(pts))
                except TypeError, ValueError:
                    logger.debug("Cannot create LineString")
        lines[route_id] = route_lines
    return lines


def dedup_analyze(
    routes: Any,
    buffer_r: float,
    thr: float = 0.70,
    per_direction: bool = False,
) -> dict[str, Any] | None:
    """Анализ перекрытий маршрутов.

    При ``per_direction=True`` направления идентифицируются стабильным
    ``DirectionKey = (route_id, direction_index)``. Локальные integer indices
    используются только внутри STRtree/NumPy.
    """
    buffer_r = _safe_float(buffer_r, default=float("nan"))
    thr = _safe_float(thr, default=float("nan"))
    if not np.isfinite(buffer_r) or buffer_r < 0.0:
        raise ValueError("buffer_r должен быть неотрицательным числом")
    if not np.isfinite(thr) or not (0.0 <= thr <= 1.0):
        raise ValueError("thr должен быть числом в диапазоне [0, 1]")
    if routes is None:
        return None

    stack = _dedup_stack()
    shapely_module = stack["shapely"]
    Transformer = stack["Transformer"]
    LineString = stack["LineString"]
    STRtree = stack["STRtree"]

    routes_geo: dict[DedupId, list[list[tuple[float, float]]]] = {}
    meta: dict[DedupId, dict[str, Any]] = {}

    if per_direction:
        from .units import build_units
        for u in build_units(routes):
            key = u.key
            try:
                coords = [(lon, lat) for lat, lon in getattr(u, "coords", [])]
            except TypeError, ValueError, AttributeError:
                logger.debug("Cannot read direction coords", exc_info=True)
                continue
            if len(coords) < 2:
                continue
            routes_geo[key] = [coords]
            meta[key] = {
                "type": _type_label(getattr(u, "route_type", "")),
                "name": getattr(u, "name", ""),
                "route_id": getattr(u, "route_id", None),
                "di": getattr(u, "di", None),
            }
    else:
        for rd in routes:
            try:
                if getattr(rd, "error", False):
                    continue
                directions = getattr(rd, "directions", None)
                if not directions:
                    continue
                route_id = int(rd.route_id)
            except TypeError, ValueError, AttributeError:
                logger.debug("Cannot parse route header", exc_info=True)
                continue
            variants: list[list[tuple[float, float]]] = []
            for d in directions:
                try:
                    coords = [(lon, lat) for lat, lon in getattr(d, "coords", [])]
                except TypeError, ValueError, AttributeError:
                    logger.debug("Cannot read direction coords", exc_info=True)
                    coords = []
                if len(coords) >= 2:
                    variants.append(coords)
            if variants:
                routes_geo[route_id] = variants
                meta[route_id] = {
                    "type": _type_label(getattr(rd, "route_type", "")),
                    "name": getattr(rd, "name", ""),
                }

    if len(routes_geo) < 2:
        return None

    lons: list[float] = []
    lats: list[float] = []
    for variants in routes_geo.values():
        for variant in variants:
            for lon, lat in variant:
                lons.append(lon)
                lats.append(lat)
    if not lons or not lats:
        return None

    epsg = utm_epsg(lons, lats)
    if epsg is None:
        logger.warning("Dedup: не удалось определить UTM-зону, пропуск")
        return None
    try:
        transformer = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    except CRSError, ProjError, TypeError, ValueError:
        logger.warning("Dedup: не удалось создать Transformer", exc_info=True)
        return None

    lines = _project_routes(routes_geo, transformer, np, LineString)
    lines = {route_id: route_lines for route_id, route_lines in lines.items() if route_lines}
    ids: list[DedupId] = list(lines.keys())
    if len(ids) < 2:
        return None

    lengths: dict[DedupId, float] = {}
    for route_id, route_lines in lines.items():
        total = sum(_safe_float(getattr(line, "length", 0.0)) for line in route_lines)
        lengths[route_id] = total

    merged: dict[DedupId, Any] = {}
    valid_ids: list[DedupId] = []
    for route_id in ids:
        if lengths.get(route_id, 0.0) <= 0.0:
            continue
        geom = union_all(lines[route_id], shapely_module)
        if geom is None or getattr(geom, "is_empty", True):
            continue
        merged[route_id] = geom
        valid_ids.append(route_id)
    ids = valid_ids
    if len(ids) < 2:
        return None

    lines = {route_id: lines[route_id] for route_id in ids}
    lengths = {route_id: lengths[route_id] for route_id in ids}
    meta = {route_id: meta.get(route_id, {}) for route_id in ids}

    if buffer_r > 0.0:
        buffer_list = [merged[route_id].buffer(buffer_r, quad_segs=4) for route_id in ids]
    else:
        buffer_list = [merged[route_id] for route_id in ids]
    for geom in buffer_list:
        shapely_module.prepare(geom)

    tree = STRtree(buffer_list)
    total_len = sum(lengths.values())
    unique_net = union_all(
        [line for route_lines in lines.values() for line in route_lines],
        shapely_module,
    )
    unique_net_len = 0.0
    if unique_net is not None and not getattr(unique_net, "is_empty", True):
        unique_net_len = _safe_float(unique_net.length)
    if unique_net_len > total_len:
        unique_net_len = total_len
    km_coef = round(total_len / unique_net_len, 2) if unique_net_len > 0.0 else 0.0

    # Только spatial layer использует локальные integer indices.
    cand_pairs: set[tuple[int, int]] = set()
    for i in range(len(ids)):
        for j in _query_indices(tree, buffer_list[i], "intersects"):
            if j > i:
                cand_pairs.add((i, j))
    cand_pairs = {
        (i, j) for i, j in cand_pairs
        if 0 <= i < len(ids) and 0 <= j < len(ids) and i < j
    }

    coverage_by_pair: dict[tuple[DedupId, DedupId], tuple[float, float]] = {}
    for i, j in cand_pairs:
        a = ids[i]
        b = ids[j]
        ca = _safe_float(_covered_length(lines[a], buffer_list[j], shapely_module))
        cb = _safe_float(_covered_length(lines[b], buffer_list[i], shapely_module))
        if ca > 0.0 or cb > 0.0:
            x, y = (a, b) if a < b else (b, a)
            coverage_by_pair[(x, y)] = (ca, cb) if a == x else (cb, ca)

    rows: list[tuple[Any, ...]] = []
    for x, y in sorted(coverage_by_pair):
        ca, cb = coverage_by_pair[(x, y)]
        k2_xy = _safe_float(ca / lengths[x]) if lengths.get(x, 0.0) > 0.0 else 0.0
        k2_yx = _safe_float(cb / lengths[y]) if lengths.get(y, 0.0) > 0.0 else 0.0
        k2_xy = min(1.0, max(0.0, k2_xy))
        k2_yx = min(1.0, max(0.0, k2_yx))
        if k2_xy == 0.0 and k2_yx == 0.0:
            continue
        rows.append((x, y, lengths.get(x, 0.0), lengths.get(y, 0.0), ca, cb, k2_xy, k2_yx))

    pairs = pd.DataFrame(
        rows,
        columns=["x", "y", "len_x", "len_y", "cov_xy", "cov_yx", "K2_xy", "K2_yx"],
    )

    if pairs.empty:
        pairs["K2_max"] = pd.Series(dtype="float64")
        pairs["Kmax"] = pd.Series(dtype="float64")
        pairs["R2"] = pd.Series(dtype="float64")
        pairs["RS"] = pd.Series(dtype="float64")
        pairs["excess"] = pd.Series(dtype="bool")
        pairs["status"] = pd.Series(dtype="string")
    else:
        for col in ["len_x", "len_y", "cov_xy", "cov_yx", "K2_xy", "K2_yx"]:
            pairs[col] = pd.to_numeric(pairs[col], errors="coerce").fillna(0.0)
        pairs["K2_max"] = pairs[["K2_xy", "K2_yx"]].max(axis=1)
        pairs["Kmax"] = pairs["K2_max"]
        max_k2 = _finite_positive_max(pairs["K2_max"])
        pairs["R2"] = pairs["K2_max"] / max_k2
        pairs["RS"] = pairs["R2"].round(3)
        pairs["excess"] = pairs["K2_max"] > thr
        pairs["status"] = np.where(pairs["excess"], "избыточно", "допустимо")

    rs_sum: dict[DedupId, float] = dict.fromkeys(ids, 0.0)
    cnt: dict[DedupId, int] = defaultdict(int)
    if not pairs.empty:
        for row in pairs[pairs["excess"]].itertuples(index=False):
            x_id = row.x
            y_id = row.y
            value = _safe_float(row.RS)
            if x_id in rs_sum:
                rs_sum[x_id] += value
                cnt[x_id] += 1
            if y_id in rs_sum:
                rs_sum[y_id] += value
                cnt[y_id] += 1

    mx = max((value for value in rs_sum.values() if np.isfinite(_safe_float(value))), default=0.0)
    if mx <= 0.0:
        mx = 1.0
    rx = {route_id: round(_safe_float(rs_sum[route_id]) / mx, 3) for route_id in ids}

    summary = pd.DataFrame([
        {
            "route_id": str(route_id),
            "тип": meta.get(route_id, {}).get("type", ""),
            "directions": len(lines.get(route_id, [])),
            "length_km": round(lengths.get(route_id, 0.0) / 1000.0, 2),
            "excess_pairs": cnt.get(route_id, 0),
            "RS_sum": round(_safe_float(rs_sum.get(route_id, 0.0)), 2),
            "Rx": rx.get(route_id, 0.0),
        }
        for route_id in ids
    ])

    return {
        "ids": ids,
        "routes_geo": routes_geo,
        "meta": meta,
        "lines": lines,
        "lengths": lengths,
        "buffer_r": buffer_r,
        "thr": thr,
        "pairs": pairs,
        "summary": summary,
        "Rx": rx,
        "total_km": round(total_len / 1000.0, 1),
        "unique_km": round(unique_net_len / 1000.0, 1),
        "km_coef": km_coef,
        "epsg": epsg,
    }


def materialize_dedup_matrix(analysis: dict[str, Any]) -> pd.DataFrame:
    """Материализует плотную K2-матрицу только для экспорта/отчёта."""
    ids: list[DedupId] = list(analysis.get("ids", []))
    pairs = analysis.get("pairs")
    matrix = np.eye(len(ids), dtype="float32")

    if not ids or pairs is None or getattr(pairs, "empty", True):
        labels = [str(route_id) for route_id in ids]
        return pd.DataFrame(matrix.round(3), index=labels, columns=labels)

    idx_map = {route_id: idx for idx, route_id in enumerate(ids)}
    xs = pairs["x"].map(idx_map)
    ys = pairs["y"].map(idx_map)
    vals = pd.to_numeric(pairs["K2_max"], errors="coerce").to_numpy(dtype=float)
    valid = xs.notna() & ys.notna() & np.isfinite(vals)
    xi = xs[valid].to_numpy(dtype=int)
    yi = ys[valid].to_numpy(dtype=int)
    k2 = np.clip(vals[valid], 0.0, 1.0)
    matrix[xi, yi] = k2
    matrix[yi, xi] = k2
    labels = [str(route_id) for route_id in ids]
    return pd.DataFrame(matrix.round(3), index=labels, columns=labels)


def dedup_network_after(
    analysis: dict[str, Any],
    active: set[DedupId],
) -> tuple[float, float, float]:
    """Считает итоговую длину сети после удаления направлений."""
    import shapely
    active_ids: set[DedupId] = set(active or set())
    kept: list[Any] = []

    for route_id, route_lines in analysis.get("lines", {}).items():
        if route_id not in active_ids:
            continue
        for line in route_lines:
            if line is None or getattr(line, "is_empty", True):
                continue
            kept.append(line)

    total = 0.0
    for line in kept:
        try:
            length = float(line.length)
        except TypeError, ValueError, AttributeError:
            continue
        if np.isfinite(length):
            total += length

    if not kept:
        return 0.0, 0.0, 0.0

    try:
        uniq_geom = union_all(kept, shapely)
    except GEOSException, TypeError, ValueError, AttributeError, RuntimeError:
        logger.debug("union_all failed in dedup_network_after", exc_info=True)
        uniq_geom = None

    uniq = 0.0
    if uniq_geom is not None and not getattr(uniq_geom, "is_empty", True):
        uniq = _safe_float(uniq_geom.length)
    if uniq > total:
        uniq = total

    return (
        round(total / 1000.0, 1),
        round(uniq / 1000.0, 1),
        round(total / uniq, 2) if uniq > 0.0 else 0.0,
    )
