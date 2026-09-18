"""Настройки и константы GIS-модуля Overture."""

import hashlib
import os


def _resolve_overture_release(release: str | None = None) -> str | None:
    value = release if release is not None else os.getenv("OVERTURE_RELEASE")
    if value is None:
        return None
    value = value.strip()
    return value or None


OVERTURE_THEME_ALIASES = {
    "places": "place",
    "buildings": "building",
    "transportation": "segment",
    "address": "address",
    "infrastructure": "infrastructure",
    "division": "division",
    "division_area": "division_area",
    "division_boundary": "division_boundary",
    "connector": "connector",
}

OVERTURE_CACHE_VERSION = 11

try:
    OVERTURE_THREAD_BATCH_SIZE = int(os.getenv("OVERTURE_THREAD_BATCH_SIZE", "0"))
except (TypeError, ValueError):
    OVERTURE_THREAD_BATCH_SIZE = 0
OVERTURE_THREAD_BATCH_MAX = 64

OVERTURE_DEDUPE_BY_GEOMETRY = True
OVERTURE_ASSUME_NO_OVERLAP = False
OVERTURE_USE_COVERAGE_UNION = False
OVERTURE_UNION_GRID_SIZE: float | None = None
OVERTURE_MIN_BUILDING_AREA_M2 = 0.0
OVERTURE_BUFFER_QUAD_SEGS = 4
OVERTURE_LINE_SIMPLIFY_M = 0.0
OVERTURE_DIRECTIONS_LATLON = True
OVERTURE_SKIP_REPAIR = os.getenv("OVERTURE_SKIP_REPAIR", "0") == "1"

try:
    OVERTURE_BUFFER_CACHE_MAX_SIZE = int(
        os.getenv("OVERTURE_BUFFER_CACHE_MAX_SIZE", "5000")
    )
except (TypeError, ValueError):
    OVERTURE_BUFFER_CACHE_MAX_SIZE = 5_000

OVERTURE_PROJECTION_CHUNK_SIZE = 50_000


def overture_algorithm_signature() -> str:
    """Возвращает сигнатуру настроек, влияющих на геометрию и расчёт площадей."""
    payload = {
        "dedupe_by_geometry": OVERTURE_DEDUPE_BY_GEOMETRY,
        "assume_no_overlap": OVERTURE_ASSUME_NO_OVERLAP,
        "use_coverage_union": OVERTURE_USE_COVERAGE_UNION,
        "union_grid_size": OVERTURE_UNION_GRID_SIZE,
        "min_building_area_m2": OVERTURE_MIN_BUILDING_AREA_M2,
        "buffer_quad_segs": OVERTURE_BUFFER_QUAD_SEGS,
        "line_simplify_m": OVERTURE_LINE_SIMPLIFY_M,
        "directions_latlon": OVERTURE_DIRECTIONS_LATLON,
        "skip_repair": OVERTURE_SKIP_REPAIR,
    }
    canonical = repr(sorted(payload.items())).encode()
    return hashlib.sha256(canonical).hexdigest()[:16]
