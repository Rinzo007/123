"""Утилиты работы с остановками.

Модуль объединяет корректировку координат и два формата агрегации уникальных
остановок.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .compat import stop_id, stop_lat, stop_lon, stop_name
from .models import RouteData, UniqueStop
from .support import fix_stop_coord, in_bbox

__all__ = [
    "collect_unique_stops",
    "collect_unique_stops_raw",
    "fix_stop_coord",
    "in_bbox",
]


def _normalize_stop_name(name: str | None) -> str:
    """Нормализует название остановки для построения стабильного ключа."""
    text = str(name or "").strip().lower()
    text = " ".join(text.split())
    return text.strip(" ,;:!?-.")


def collect_unique_stops(
    routes: Iterable[RouteData],
) -> dict[str, UniqueStop]:
    """Собирает уникальные остановки в структурированные ``UniqueStop``."""
    temp: dict[str, dict[str, Any]] = {}

    for route in routes:
        if not route.ok:
            continue
        for direction in route.directions:
            for stop in direction.stops:
                key = (
                    str(stop.id)
                    if stop.id is not None
                    else f"name:{_normalize_stop_name(stop.name)}"
                )
                record = temp.setdefault(
                    key,
                    {
                        "id": stop.id,
                        "name": stop.name,
                        "latitude": stop.latitude,
                        "longitude": stop.longitude,
                        "types": set(),
                        "routes": set(),
                    },
                )
                record["types"].add(route.route_type)
                record["routes"].add(route.route_id)
                if record["latitude"] is None and stop.latitude is not None:
                    record["latitude"] = stop.latitude
                    record["longitude"] = stop.longitude

    return {
        key: UniqueStop(
            key=key,
            id=record["id"],
            name=record["name"],
            latitude=record["latitude"],
            longitude=record["longitude"],
            route_types=frozenset(record["types"]),
            route_ids=frozenset(record["routes"]),
        )
        for key, record in temp.items()
    }


def collect_unique_stops_raw(
    routes: Iterable[RouteData],
) -> dict[str, dict[str, Any]]:
    """Собирает уникальные остановки в raw-формате для экспортного слоя."""
    unique: dict[str, dict[str, Any]] = {}
    for route in routes:
        if route.error or not route.directions:
            continue
        for direction in route.directions:
            for stop_index, stop in enumerate(direction.stops):
                sid = stop_id(stop)
                name = stop_name(stop)
                key = (
                    str(sid)
                    if sid is not None
                    else "name:" + _normalize_stop_name(name)
                )
                record = unique.get(key)
                if record is None:
                    record = unique[key] = {
                        "id": sid,
                        "name": name,
                        "lat": stop_lat(stop),
                        "lon": stop_lon(stop),
                        "idx": stop_index,
                        "types": set(),
                        "routes": set(),
                    }
                elif record["lat"] is None:
                    lat = stop_lat(stop)
                    lon = stop_lon(stop)
                    if lat is not None and lon is not None:
                        record["lat"] = lat
                        record["lon"] = lon
                        record["idx"] = stop_index
                record["types"].add(route.route_type)
                record["routes"].add(route.route_id)
    return unique
