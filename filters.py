"""Фильтрация маршрутов по bbox, радиусу, кривизне и длине."""

import math
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING

from .geometry import haversine_km
from .models import BBox, FilterLimits, RouteData

if TYPE_CHECKING:
    pass

__all__ = [
    "apply_route_limits",
    "compute_bbox",
    "route_exceeds_radius",
]


def compute_bbox(
    routes: Iterable[RouteData],
    buffer_deg: float | None = None,
) -> BBox | None:
    """Вычисляет bounding box по всем координатам маршрутов.

    По умолчанию bbox ровно по границам координат; ``buffer_deg`` добавляет
    отступ в градусах.

    Возвращает ``None``, если не найдено ни одной валидной координаты.

    ИСПРАВЛЕНО (L-01): результирующие координаты clamped в валидные диапазоны
    (lat ∈ [-90, 90], lon ∈ [-180, 180]), чтобы буфер не вывел bbox за пределы
    географической системы координат.
    """
    min_lat = math.inf
    min_lon = math.inf
    max_lat = -math.inf
    max_lon = -math.inf
    found = False

    for route in routes:
        for direction in route.directions:
            for lat, lon in direction.coords:
                if not (math.isfinite(lat) and math.isfinite(lon)):
                    continue

                found = True

                min_lat = min(min_lat, lat)
                max_lat = max(max_lat, lat)
                min_lon = min(min_lon, lon)
                max_lon = max(max_lon, lon)

    if not found:
        return None

    buffer_deg = max(0.0, float(buffer_deg)) if buffer_deg is not None else 0.0

    # ИСПРАВЛЕНО (L-01): clamp в валидные географические диапазоны.
    return BBox(
        min_lat=max(-90.0, min_lat - buffer_deg),
        min_lon=max(-180.0, min_lon - buffer_deg),
        max_lat=min(90.0, max_lat + buffer_deg),
        max_lon=min(180.0, max_lon + buffer_deg),
    )


def route_exceeds_radius(
    route: RouteData,
    center_lat: float,
    center_lon: float,
    radius_km: float,
) -> bool:
    """Проверяет, выходит ли маршрут за пределы круга заданного радиуса.

    Алгоритм оптимизирован:
    1. Если все углы bbox маршрута внутри радиуса → маршрут точно внутри.
    2. Если ближайшая точка bbox (проекция центра) вне радиуса → маршрут точно вне.
    3. Иначе — перебор всех точек маршрута (fallback).

    Возвращает ``False`` для маршрутов без координат.
    """
    coords = [coord for direction in route.directions for coord in direction.coords]

    if not coords:
        return False

    lats = [lat for lat, _ in coords]
    lons = [lon for _, lon in coords]

    min_lat, max_lat = min(lats), max(lats)
    min_lon, max_lon = min(lons), max(lons)

    corners = [
        (min_lat, min_lon),
        (min_lat, max_lon),
        (max_lat, min_lon),
        (max_lat, max_lon),
    ]

    corner_distances = [
        haversine_km(center_lat, center_lon, lat, lon) for lat, lon in corners
    ]

    # Оптимизация: если все углы внутри радиуса, маршрут точно внутри.
    if max(corner_distances) <= radius_km:
        return False

    # Оптимизация: если ближайшая точка bbox вне радиуса, маршрут точно вне.
    closest_lat = max(min_lat, min(center_lat, max_lat))
    closest_lon = max(min_lon, min(center_lon, max_lon))

    if haversine_km(center_lat, center_lon, closest_lat, closest_lon) > radius_km:
        return True

    # Fallback: перебор всех точек маршрута.
    return any(
        haversine_km(center_lat, center_lon, lat, lon) > radius_km
        for lat, lon in coords
    )


def apply_route_limits(
    routes: Sequence[RouteData],
    limits: FilterLimits,
) -> tuple[list[RouteData], list[tuple[RouteData, str]]]:
    """Применяет фильтры по кривизне, длине и радиусу.

    Возвращает кортеж ``(kept, excluded)``, где ``excluded`` содержит
    пары ``(route, reason)`` с причинами исключения.

    Фильтр по радиусу применяется только если ``radius_km > 0`` и оба
    центра (``center_lat``, ``center_lon``) заданы.
    """
    kept: list[RouteData] = []
    excluded: list[tuple[RouteData, str]] = []

    for route in routes:
        if limits.curvilinearity > 0 and route.curvilinearity > limits.curvilinearity:
            excluded.append((route, "криволинейность"))
            continue

        if limits.min_length_km > 0 and route.min_km < limits.min_length_km:
            excluded.append((route, "длина"))
            continue

        if (
            limits.radius_km > 0
            and limits.center_lat is not None
            and limits.center_lon is not None
            and route_exceeds_radius(
                route,
                limits.center_lat,
                limits.center_lon,
                limits.radius_km,
            )
        ):
            excluded.append((route, "радиус"))
            continue

        kept.append(route)

    return kept, excluded
