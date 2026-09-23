"""Настройки Overture и обратная совместимость со старым API."""

from .config import OvertureConfig

# Один объект-конфигурация является источником истины для нового кода.
DEFAULT_CONFIG = OvertureConfig.from_env()

# Старые имена оставлены как совместимые алиасы. Внутри pipeline они больше
# не используются: фактическая конфигурация приходит через OvertureConfig.
OVERTURE_CACHE_VERSION = DEFAULT_CONFIG.cache_version
OVERTURE_THREAD_BATCH_SIZE = DEFAULT_CONFIG.thread_batch_size
OVERTURE_THREAD_BATCH_MAX = DEFAULT_CONFIG.thread_batch_max
OVERTURE_DEDUPE_BY_GEOMETRY = DEFAULT_CONFIG.dedupe_by_geometry
OVERTURE_ASSUME_NO_OVERLAP = DEFAULT_CONFIG.assume_no_overlap
OVERTURE_USE_COVERAGE_UNION = DEFAULT_CONFIG.use_coverage_union
OVERTURE_UNION_GRID_SIZE = DEFAULT_CONFIG.union_grid_size
OVERTURE_MIN_BUILDING_AREA_M2 = DEFAULT_CONFIG.min_building_area_m2
OVERTURE_BUFFER_QUAD_SEGS = DEFAULT_CONFIG.buffer_quad_segs
OVERTURE_LINE_SIMPLIFY_M = DEFAULT_CONFIG.line_simplify_m
OVERTURE_DIRECTIONS_LATLON = DEFAULT_CONFIG.directions_latlon
OVERTURE_SKIP_REPAIR = DEFAULT_CONFIG.skip_repair
OVERTURE_BUFFER_CACHE_MAX_SIZE = DEFAULT_CONFIG.buffer_cache_max_size
OVERTURE_PROJECTION_CHUNK_SIZE = DEFAULT_CONFIG.projection_chunk_size

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


def _resolve_overture_release(release: str | None = None) -> str | None:
    """Совместимый фасад единого resolver'а release Overture."""
    from .release import resolve_overture_release

    try:
        return resolve_overture_release(release)
    except Exception:
        # Старый приватный API исторически возвращал None при отсутствии
        # возможности определить release; сохраняем это поведение только
        # для legacy-вызовов, новый loader использует строгий resolver.
        return None
