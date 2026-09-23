"""Зоны OD-матрицы: сетка по границе города и веса (GHS-растр/районы).

Публичный интерфейс модуля ``zones`` сохранён: имена реэкспортируются
из подмодуля ``source``.
"""

from __future__ import annotations

from .source import (
    _DEFAULT_ZONE_SIZE_M,
    _GEO_PAD_DEG,
    _MAX_DISTRICT_RADIUS_M,
    _MIN_CLIP_AREA_RATIO,
    _PROJECTED_PAD_M,
    _cell_step,
    _zone_tile_sums,
    assign_district_names,
    build_zones,
    load_districts,
    load_zones_from_file,
    zonal_weights,
)

__all__ = [
    "_DEFAULT_ZONE_SIZE_M",
    "_GEO_PAD_DEG",
    "_MAX_DISTRICT_RADIUS_M",
    "_MIN_CLIP_AREA_RATIO",
    "_PROJECTED_PAD_M",
    "_cell_step",
    "_zone_tile_sums",
    "assign_district_names",
    "build_zones",
    "load_districts",
    "load_zones_from_file",
    "zonal_weights",
]