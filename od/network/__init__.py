"""Дорожная сеть города: OSM (Overpass) и Overture сегменты.

Публичный интерфейс модуля ``network`` сохранён: имена реэкспортируются
из подмодуля ``osm``.
"""

from __future__ import annotations

from .osm import (
    _FALLBACK_SPEED_KMH,
    OD_ROADS_CACHE_ENTRY_BYTES,
    _snapped_centroids,
    build_road_graph,
    euclidean_costs,
    fetch_road_ways,
    ways_from_overpass,
    ways_from_overture,
    zone_network_costs,
)

__all__ = [
    "OD_ROADS_CACHE_ENTRY_BYTES",
    "_FALLBACK_SPEED_KMH",
    "_snapped_centroids",
    "build_road_graph",
    "euclidean_costs",
    "fetch_road_ways",
    "ways_from_overpass",
    "ways_from_overture",
    "zone_network_costs",
]