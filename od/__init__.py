"""Расчёт матрицы корреспонденций (OD) по транспортным зонам города.

Пакет разделён на подмодули: базовые типы/примитивы (``model``), дорожная
сеть (``network``), зоны и веса (``zones``), спрос Takt (``demand``),
построение матриц (``builders``) и ввод/вывод со стадией (``io``).
"""

from __future__ import annotations

from .builders import (
    _SPARSE_GRAVITY_CELLS,
    _furness,
    _furness_sparse,
    build_gravity_od,
    build_purpose_od,
    build_takt_demand_with_purposes,
    build_takt_od,
    periods_from_purpose_blend,
)
from .demand import (
    _write_demand_street_geojson,
    load_demand_streets,
    load_takt_demand,
    load_takt_purposes,
    write_takt_demand,
    write_takt_purposes,
    zone_weights_from_streets,
)
from .io import (
    _OD_CACHE_SCHEMA_VERSION,
    assign_road_loads,
    load_od_from_files,
    run_od_stage,
    save_od_outputs,
)
from .model import (
    OdMatrixError,
    OdResult,
    PurposeOd,
    TaktDemand,
    TaktPurposeLayer,
    TaktPurposes,
    Zones,
    _as_sparse,
    _cosscale,
    haversine_km,
)
from .network import (
    _FALLBACK_SPEED_KMH,
    _snapped_centroids,
    build_road_graph,
    euclidean_costs,
    fetch_road_ways,
    ways_from_overpass,
    ways_from_overture,
    zone_network_costs,
)
from .zones import (
    _cell_step,
    _zone_tile_sums,
    assign_district_names,
    build_zones,
    load_districts,
    load_zones_from_file,
    zonal_weights,
)

__all__ = [
    "_FALLBACK_SPEED_KMH",
    "_OD_CACHE_SCHEMA_VERSION",
    "_SPARSE_GRAVITY_CELLS",
    "OdMatrixError",
    "OdResult",
    "PurposeOd",
    "TaktDemand",
    "TaktPurposeLayer",
    "TaktPurposes",
    "Zones",
    "_as_sparse",
    "_cell_step",
    "_cosscale",
    "_furness",
    "_furness_sparse",
    "_snapped_centroids",
    "_write_demand_street_geojson",
    "_zone_tile_sums",
    "assign_district_names",
    "assign_road_loads",
    "build_gravity_od",
    "build_purpose_od",
    "build_road_graph",
    "build_takt_demand_with_purposes",
    "build_takt_od",
    "build_zones",
    "euclidean_costs",
    "fetch_road_ways",
    "haversine_km",
    "load_demand_streets",
    "load_districts",
    "load_od_from_files",
    "load_takt_demand",
    "load_takt_purposes",
    "load_zones_from_file",
    "periods_from_purpose_blend",
    "run_od_stage",
    "save_od_outputs",
    "ways_from_overpass",
    "ways_from_overture",
    "write_takt_demand",
    "write_takt_purposes",
    "zonal_weights",
    "zone_network_costs",
    "zone_weights_from_streets",
]