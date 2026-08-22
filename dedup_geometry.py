"""Геометрические и пространственные примитивы дедупликации.

Модуль не содержит policy-логики и не знает о порядке удаления направлений.
Он отвечает только за подготовку геометрии, spatial prefilter и операции
пересечения.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

import numpy as np
import pandas as pd
from pyproj.exceptions import CRSError, ProjError
from shapely.errors import GEOSException

logger = logging.getLogger("wikiroutes.gis.dedup.geometry")


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if not np.isfinite(result):
        return default
    return result


def finite_positive_max(series: pd.Series) -> float:
    if len(series) == 0:
        return 1.0
    value = safe_float(series.max(), default=1.0)
    return value if value > 0.0 else 1.0


def line_parts(geom: Any) -> Iterator[Any]:
    if geom is None:
        return
    try:
        if geom.is_empty:
            return
    except (AttributeError, TypeError):
        return

    geom_type = getattr(geom, "geom_type", "")
    if geom_type == "LineString":
        yield geom
    elif geom_type == "MultiLineString":
        try:
            yield from geom.geoms
        except (AttributeError, TypeError):
            return
    elif geom_type == "GeometryCollection":
        try:
            for part in geom.geoms:
                yield from line_parts(part)
        except (AttributeError, TypeError):
            return


def query_indices(tree: Any, geom: Any, predicate: str = "intersects") -> list[int]:
    if geom is None:
        return []
    try:
        if geom.is_empty:
            return []
    except (AttributeError, TypeError):
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
        except (TypeError, ValueError, OverflowError):
            continue
        if idx >= 0:
            out.append(idx)
    return out


def bounds_overlap(
    bounds_a: tuple[float, float, float, float],
    bounds_b: tuple[float, float, float, float],
) -> bool:
    minx_a, miny_a, maxx_a, maxy_a = bounds_a
    minx_b, miny_b, maxx_b, maxy_b = bounds_b
    return not (
        maxx_a < minx_b or maxx_b < minx_a or maxy_a < miny_b or maxy_b < miny_a
    )


def covered_length_loop(lines: Any, other_buffer: Any) -> float:
    total = 0.0
    for line in lines:
        if line is None or getattr(line, "is_empty", True):
            continue
        try:
            intersection = line.intersection(other_buffer)
            for part in line_parts(intersection):
                try:
                    length = float(part.length)
                except (TypeError, ValueError, AttributeError):
                    continue
                if np.isfinite(length):
                    total += length
        except (GEOSException, TypeError, ValueError, AttributeError, RuntimeError):
            continue
    return total


def covered_length(lines: Any, other_buffer: Any, shapely_module: Any) -> float:
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
            if not bounds_overlap(line.bounds, buffer_bounds):
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
            except (GEOSException, TypeError, ValueError, RuntimeError):
                return covered_length_loop(keep, other_buffer)

        total = 0.0
        for geom in np.atleast_1d(intersection):
            try:
                length = float(geom.length)
            except (TypeError, ValueError, AttributeError):
                continue
            if np.isfinite(length):
                total += length
        return total
    except (GEOSException, TypeError, ValueError, AttributeError, RuntimeError) as exc:
        logger.warning("Intersection failed in covered_length for %s", type(exc).__name__)
        return 0.0


def dedupe_xy_loop(
    points: list[tuple[float, float]],
    eps: float,
) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    last_grid: tuple[float, float] | None = None
    for x, y in points:
        if not (np.isfinite(x) and np.isfinite(y)):
            continue
        try:
            gx = round(x / eps) * eps
            gy = round(y / eps) * eps
        except (TypeError, ValueError, OverflowError):
            continue
        grid_key = (gx, gy)
        if last_grid != grid_key:
            out.append((x, y))
            last_grid = grid_key
    return out


def dedupe_xy(
    points: list[tuple[float, float]],
    eps: float = 0.01,
) -> list[tuple[float, float]]:
    if eps <= 0.0:
        return list(points)
    try:
        arr = np.asarray(points, dtype=np.float64)
    except (TypeError, ValueError, OverflowError):
        arr = None
    if arr is None or arr.ndim != 2 or arr.shape[1] < 2 or arr.shape[0] == 0:
        return dedupe_xy_loop(points, eps)
    finite = np.isfinite(arr).all(axis=1)
    if not finite.any():
        return []
    arr = arr[finite]
    grid = np.round(arr / eps) * eps
    keep = np.empty(arr.shape[0], dtype=bool)
    keep[0] = True
    keep[1:] = (grid[1:] != grid[:-1]).any(axis=1)
    return [(float(x), float(y)) for x, y in arr[keep]]


def project_routes(
    routes_geo: dict[Any, list[list[tuple[float, float]]]],
    transformer: Any,
    np_module: Any,
    line_string_cls: Any,
) -> dict[Any, list[Any]]:
    lines: dict[Any, list[Any]] = {}
    for route_id, variants in routes_geo.items():
        route_lines: list[Any] = []
        for variant in variants:
            if len(variant) < 2:
                continue
            try:
                arr = np_module.asarray(variant, dtype=np.float64)
            except (TypeError, ValueError):
                continue
            if arr.ndim != 2 or arr.shape[0] < 2 or arr.shape[1] < 2:
                continue
            try:
                xs, ys = transformer.transform(arr[:, 0], arr[:, 1])
            except (CRSError, ProjError, TypeError, ValueError, RuntimeError):
                continue
            xs = np_module.asarray(xs, dtype=np.float64)
            ys = np_module.asarray(ys, dtype=np.float64)
            mask = np_module.isfinite(xs) & np_module.isfinite(ys)
            if not mask.any():
                continue
            xs = xs[mask]
            ys = ys[mask]
            pts = dedupe_xy(list(zip(xs.tolist(), ys.tolist(), strict=True)))
            if len(pts) < 2:
                pts = list(zip(xs.tolist(), ys.tolist(), strict=True))
            if len(pts) >= 2:
                try:
                    route_lines.append(line_string_cls(pts))
                except (TypeError, ValueError):
                    continue
        lines[route_id] = route_lines
    return lines
