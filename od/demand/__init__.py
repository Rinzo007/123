"""Спрос и матрицы Takt: загрузка demand-файлов и веса по улицам.

Публичный интерфейс модуля ``demand`` сохранён: имена реэкспортируются
из подмодуля ``takt``.
"""

from __future__ import annotations

from .takt import (
    _write_demand_street_geojson,
    load_demand_streets,
    load_takt_demand,
    zone_weights_from_streets,
)

__all__ = [
    "_write_demand_street_geojson",
    "load_demand_streets",
    "load_takt_demand",
    "zone_weights_from_streets",
]