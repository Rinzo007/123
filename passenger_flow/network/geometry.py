"""Геометрические вычисления для расчёта пассажиропотока.

Привязка зон к ближайшим остановкам, расстояние по гаверсинусу,
канонический ключ физической остановки и проверка совместимости
остановок для пересадки.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from scipy.spatial import cKDTree

from ..base.takt import (
    _TAKT_TRANSFER_MAX_WALK_M,
    _TAKT_WALK_BASE_S,
    _TAKT_WALK_DISUT_PER_M_S,
    _TAKT_WALK_PER_M_S,
    _TAKT_WALK_SPEED_MPS,
)

_STOP_SEARCH_RADIUS_M = 1500.0


def haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Расстояние между точками по формуле гаверсинуса, метры."""
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2.0) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2.0) ** 2
    )
    return 6_371_000.0 * 2.0 * math.asin(math.sqrt(a))


def _takt_transfer_penalty_min(
    lat1: float, lon1: float, lat2: float, lon2: float
) -> float:
    """Дискомфорт пешей пересадки по Takt (мин).

    Точная формула движка: ``vn(d) + d/ae``, где ``vn(m) = Uo + ec*m``
    секунд (Uo = 405, ec = 0.25), скорость ходьбы ae = 1.33 м/с, потолок
    ходьбы между платформами Za = 800 м.
    """
    walk_m = min(haversine_meters(lat1, lon1, lat2, lon2), _TAKT_TRANSFER_MAX_WALK_M)
    return (
        _TAKT_WALK_BASE_S
        + walk_m * _TAKT_WALK_PER_M_S
        + walk_m * _TAKT_WALK_DISUT_PER_M_S / _TAKT_WALK_SPEED_MPS
    ) / 60.0


def _stop_key(stop: dict[str, Any]) -> str:
    """Канонический ключ физической остановки: id или округлённые координаты."""
    if stop.get("id") is not None:
        return f"id:{stop['id']}"
    return f"{stop['lat']:.5f},{stop['lon']:.5f}"


def _transfers_match(
    a: dict[str, Any],
    b: dict[str, Any],
    radius_m: float,
) -> bool:
    """Совместимы ли остановки для пересадки (id совпадает или точки в радиусе)."""
    if a.get("id") is not None and a["id"] == b.get("id"):
        return True
    return (
        haversine_meters(float(a["lat"]), float(a["lon"]), float(b["lat"]), float(b["lon"]))
        <= radius_m
    )


def _find_nearest_stops(
    lat: float,
    lon: float,
    stop_coords: np.ndarray,
    tree: cKDTree,
    radius_m: float = _STOP_SEARCH_RADIUS_M,
) -> list[int]:
    """Находит индексы остановок в радиусе ``radius_m`` метров.

    Дерево построено в степенях (lon, lat), поэтому порог поиска задаётся
    в градусах, а кандидаты затем отсеиваются по фактическому расстоянию
    (формула гаверсинуса) в метрах.
    """
    if len(stop_coords) == 0:
        return []
    lat_deg = radius_m / 111_320.0
    lon_deg = lat_deg / max(math.cos(math.radians(lat)), 0.2)
    search_bound = math.hypot(lon_deg, lat_deg)
    # query_ball_point возвращает все точки в пределах квадрата поиска без
    # произвольного ограничения k; итоговую отсечку делает гаверсинус.
    candidates = tree.query_ball_point([lat, lon], r=search_bound)
    result: list[int] = []
    for fi in candidates:
        stop_lat, stop_lon = stop_coords[fi]
        if haversine_meters(lat, lon, float(stop_lat), float(stop_lon)) <= radius_m:
            result.append(int(fi))
    return result