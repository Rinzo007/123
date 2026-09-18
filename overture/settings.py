"""Настройки и константы GIS-модуля Overture."""

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

OVERTURE_CACHE_VERSION = 10

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
