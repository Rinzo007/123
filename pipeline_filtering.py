"""Функции стадии фильтрации маршрутов."""

from __future__ import annotations

from collections.abc import Sequence

from .config import CliConfig
from .filters import apply_route_limits
from .models import FilterLimits, RouteData


def build_filter_limits(config: CliConfig) -> FilterLimits:
    """Нормализует параметры фильтрации из CLI-конфигурации."""
    curvilinearity = config.curv if config.curv is not None else 0.0
    min_length = config.minlen if config.minlen is not None else 0.0
    radius = config.radius if config.radius is not None else 0.0
    center_lat = config.center_lat
    center_lon = config.center_lon

    if radius > 0 and (center_lat is None or center_lon is None):
        radius = 0.0
        center_lat = None
        center_lon = None

    return FilterLimits(
        curvilinearity=curvilinearity,
        min_length_km=min_length,
        radius_km=radius,
        center_lat=center_lat,
        center_lon=center_lon,
    )


def split_active_routes(
    routes: Sequence[RouteData],
    *,
    active_only: bool,
) -> tuple[list[RouteData], int]:
    """Возвращает активные маршруты и число пропущенных неактивных."""
    if not active_only:
        return list(routes), 0

    active = [route for route in routes if route.active]
    return active, len(routes) - len(active)


def split_ok_and_errors(
    routes: Sequence[RouteData],
) -> tuple[list[RouteData], list[RouteData]]:
    """Разделяет маршруты на успешные и ошибочные."""
    ok = [route for route in routes if not route.error and route.directions]
    bad = [route for route in routes if route.error]
    return ok, bad


def apply_limits(
    routes: Sequence[RouteData],
    limits: FilterLimits,
) -> tuple[list[RouteData], list[tuple[RouteData, str]]]:
    """Применяет геометрические/числовые ограничения к маршрутам."""
    return apply_route_limits(routes, limits)


__all__ = [
    "apply_limits",
    "build_filter_limits",
    "split_active_routes",
    "split_ok_and_errors",
]
