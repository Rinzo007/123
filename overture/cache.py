"""LRU-кэш и потокобезопасный доступ к дисковому кэшу Overture."""

import hashlib
import logging
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

from ..cache import JsonCache
from ..metrics import OvertureStats

logger = logging.getLogger("wikiroutes.gis.overture")


class LRUCache:
    """Простой LRU-кэш с ограничением размера."""

    def __init__(self, max_size: int = 5_000):
        self.data: OrderedDict[Any, Any] = OrderedDict()
        self.max_size = max(1, int(max_size))

    def get(self, key: Any) -> Any:
        if key not in self.data:
            return None
        self.data.move_to_end(key)
        return self.data[key]

    def put(self, key: Any, value: Any) -> None:
        self.data[key] = value
        self.data.move_to_end(key)
        if len(self.data) > self.max_size:
            self.data.popitem(last=False)

    def __len__(self) -> int:
        return len(self.data)


def _safe_bbox_key(bbox: tuple[float, float, float, float]) -> str:
    return hashlib.blake2b(
        f"{bbox[0]:.6f},{bbox[1]:.6f},{bbox[2]:.6f},{bbox[3]:.6f}".encode(),
        digest_size=8,
    ).hexdigest()[:12]


def _file_signature(paths: list[str]) -> str:
    parts: list[str] = []
    for raw_path in sorted(paths):
        path = Path(raw_path)
        try:
            stat = path.stat()
            parts.append(f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}")
        except OSError:
            parts.append(str(path.resolve()))
    return hashlib.sha256(";".join(parts).encode()).hexdigest()[:16]


def _cache_get(cache: JsonCache | None, lock: threading.RLock | None, key: str) -> Any:
    if cache is None:
        return None
    try:
        if lock is not None:
            with lock:
                return cache.get("overture", key)
        return cache.get("overture", key)
    except (OSError, TypeError, ValueError, RuntimeError):
        logger.exception("Overture: ошибка чтения кэша для ключа %s", key)
        return None


def _cache_put(
    cache: JsonCache | None, lock: threading.RLock | None, key: str, value: Any
) -> None:
    if cache is None:
        return
    try:
        if lock is not None:
            with lock:
                cache.put("overture", key, value)
        else:
            cache.put("overture", key, value)
    except (OSError, TypeError, ValueError, RuntimeError):
        logger.exception("Overture: ошибка записи кэша для ключа %s", key)


def _stats_to_cached(st: OvertureStats) -> dict[str, Any]:
    """Сериализует статистику в стабильный JSON-совместимый формат."""
    return {
        "total_area_m2": float(max(0.0, st.total_area_m2)),
        "corridor_m2": float(max(0.0, st.corridor_m2)),
        "count": int(max(0, st.count)),
        "ok": bool(st.ok),
    }


def _stats_from_cached(cached: Any) -> OvertureStats:
    if not isinstance(cached, dict):
        return OvertureStats(ok=False)
    try:
        return OvertureStats(
            total_area_m2=max(0.0, float(cached.get("total_area_m2", 0.0))),
            corridor_m2=max(0.0, float(cached.get("corridor_m2", 0.0))),
            count=max(0, int(cached.get("count", 0) or 0)),
            ok=bool(cached.get("ok", True)),
        )
    except (TypeError, ValueError, OverflowError):
        return OvertureStats(ok=False)


def _route_stats_from_cached(
    cached: Any,
) -> tuple[OvertureStats, dict[int, OvertureStats]] | None:
    """Читает атомарный route-cache с результатами маршрута и направлений."""
    if not isinstance(cached, dict):
        return None
    route_payload = cached.get("route")
    directions_payload = cached.get("directions")
    if not isinstance(route_payload, dict) or not isinstance(directions_payload, dict):
        return None

    route_stats = _stats_from_cached(route_payload)
    dir_stats: dict[int, OvertureStats] = {}
    try:
        for key, payload in directions_payload.items():
            dir_stats[int(key)] = _stats_from_cached(payload)
    except (AttributeError, TypeError, ValueError):
        return None
    return route_stats, dir_stats
