"""Конвейер расчёта: стадии от каталога до данных для экспорта.

Промежуточные результаты типизированы (``PipelineResult``), а функции
стадий, не требующие сети, вынесены отдельно и тестируются без сети
(``build_base_tasks``, ``build_secondary_tasks``, ``select_secondary_routes``,
``compute_pipeline_bbox``).

Сообщения стадий пишутся в ``PipelineContext.reporter`` — конвейер ничего
не печатает напрямую.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any

from .cache import JsonCache
from .catalog import load_catalog
from .config import CliConfig
from .dedup import dedup_analyze, dedup_network_after
from .dedup_policy import dedup_compute_removals
from .enums import RouteType, type_label
from .errors import CatalogLoadError
from .filters import apply_route_limits, compute_bbox
from .generate import (
    _stops_area_km2,
    compute_stop_volumes,
    gen_route_count_formula,
    generate_routes_network,
)
from .ghs import compute_ghs, compute_ghs_s
from .heatmap import build_heatmap
from .http_client import SessionProvider
from .ideas import ideas_to_routes, load_ideas
from .metrics import BuiltSStats, GhsStats, OvertureStats, PoiStats
from .models import FilterLimits, RouteData, RouteTask
from .overture import auto_download_overture, compute_overture
from .poi import compute_poi
from .report import Reporter
from .routes import CachedNetworkRouteFetcher, download_routes
from .stops import collect_unique_stops_raw
from .support import route_passes_number_filter
from .units import build_units, network_center_distances

__all__ = [
    "BASE_ROUTE_TYPES",
    "PipelineContext",
    "PipelineResult",
    "build_base_tasks",
    "build_secondary_tasks",
    "compute_pipeline_bbox",
    "run_pipeline",
    "select_secondary_routes",
]

# Базовая группа типов – по ним строится bbox
BASE_ROUTE_TYPES = frozenset(
    {
        RouteType.TRAM,
        RouteType.TROLLEYBUS,
        RouteType.METRO,
        RouteType.ELECTROBUS,
    }
)


@dataclass
class PipelineContext:
    """Зависимости конвейера: кэш, HTTP-сессии и отчёт о ходе."""

    cache: JsonCache
    sessions: SessionProvider
    reporter: Reporter = field(default_factory=Reporter)


@dataclass
class PipelineResult:
    """Все вычисленные данные конвейера, потребляемые экспортом."""

    city_slug: str
    city_title: str
    base_task_count: int
    secondary_task_count: int
    base_routes: list[RouteData]
    secondary_routes: list[RouteData]
    all_routes: list[RouteData]
    ok_routes: list[RouteData]
    bad_routes: list[RouteData]
    bbox: tuple[float, float, float, float] | None
    limits: FilterLimits
    excluded_routes: list[tuple[RouteData, str]]
    excluded_counts: dict[str, int]
    skipped_inactive: int
    ghs_stats: dict[int, GhsStats]
    ghs_meta: dict[str, Any] | None
    ghs_dir_stats: dict[tuple[int, int], GhsStats]
    built_s_stats: dict[int, BuiltSStats]
    built_s_meta: dict[str, Any] | None
    built_s_dir_stats: dict[tuple[int, int], BuiltSStats]
    overture_stats: dict[int, OvertureStats]
    overture_meta: dict[str, Any] | None
    overture_dir_stats: dict[tuple[int, int], OvertureStats]
    poi_stats: dict[int, PoiStats]
    poi_values: dict[str, float]
    poi_dir_stats: dict[tuple[int, int], PoiStats]
    dedup_removed: list[dict[str, Any]] | None
    dedup_analysis: dict[str, Any] | None
    net_metrics: dict[str, Any] | None
    unique_stops: dict[str, dict[str, Any]]
    generated_routes: list[dict[str, Any]]
    stop_volumes: dict[str, float] | None
    heatmap: dict[str, Any] | None


# ── Стадии-помощники (чистые, тестируемые без сети) ─────────────────────


def _base_types(config: CliConfig) -> set[RouteType]:
    return (
        set(BASE_ROUTE_TYPES)
        if not config.type_filter
        else set(config.type_filter) & set(BASE_ROUTE_TYPES)
    )


def _secondary_types(config: CliConfig) -> set[RouteType]:
    all_types = set(RouteType)

    return (
        all_types - set(BASE_ROUTE_TYPES)
        if not config.type_filter
        else set(config.type_filter) - set(BASE_ROUTE_TYPES)
    )


def build_base_tasks(
    catalog: Any,
    city_slug: str,
    config: CliConfig,
) -> list[RouteTask]:
    """Строит задачи загрузки для базовых типов (по ним строится bbox)."""
    base_types = _base_types(config)
    route_filter = config.route_filter.strip().lower() if config.route_filter else None

    tasks: list[RouteTask] = []

    for section in catalog.sections:
        if section.route_type not in base_types:
            continue

        if section.route_type in config.disabled_types:
            continue

        for link in section.links:
            if route_filter and not (
                link.name.strip().lower() == route_filter
                or str(link.route_id) == route_filter
            ):
                continue

            if not route_passes_number_filter(link.name, config.max_route_number):
                continue

            tasks.append(
                RouteTask(
                    city=city_slug,
                    route_type=section.route_type,
                    name=link.name,
                    route_id=link.route_id,
                )
            )

    return tasks


def build_secondary_tasks(
    catalog: Any,
    city_slug: str,
    config: CliConfig,
) -> list[RouteTask]:
    """Строит задачи загрузки для вторичных типов (фильтруются по bbox)."""
    secondary_types = _secondary_types(config)
    route_filter = config.route_filter.strip().lower() if config.route_filter else None

    tasks: list[RouteTask] = []

    if secondary_types:
        for section in catalog.sections:
            if section.route_type not in secondary_types:
                continue

            if section.route_type in config.disabled_types:
                continue

            for link in section.links:
                if route_filter and not (
                    link.name.strip().lower() == route_filter
                    or str(link.route_id) == route_filter
                ):
                    continue

                if not route_passes_number_filter(link.name, config.max_route_number):
                    continue

                tasks.append(
                    RouteTask(
                        city=city_slug,
                        route_type=section.route_type,
                        name=link.name,
                        route_id=link.route_id,
                    )
                )

    return tasks


def compute_pipeline_bbox(
    base_routes: list[RouteData],
    buffer_deg: float | None,
) -> tuple[float, float, float, float] | None:
    """Вычисляет bbox по валидным базовым маршрутам в виде кортежа."""
    base_ok_routes = [
        route for route in base_routes if not route.error and route.directions
    ]
    bbox_result = compute_bbox(base_ok_routes, buffer_deg=buffer_deg)

    if bbox_result is None:
        return None

    return (
        bbox_result.min_lat,
        bbox_result.min_lon,
        bbox_result.max_lat,
        bbox_result.max_lon,
    )


def select_secondary_routes(
    loaded: list[RouteData],
    bbox: tuple[float, float, float, float] | None,
    no_bbox_filter: bool,
) -> list[RouteData]:
    """Применяет строгий bbox-фильтр к вторичным маршрутам."""
    if no_bbox_filter:
        return list(loaded)

    if bbox is None:
        return []

    return [route for route in loaded if _route_inside_bbox(route, bbox)]


# ── Помощники дедупликации (перенесены из cli.py) ───────────────────────


def _apply_dedup_removals(
    routes: list[RouteData],
    meta: dict[int, dict[str, Any]],
    removed_uids: set[int],
) -> tuple[list[RouteData], int, int]:
    """Удаляет направления из маршрутов после дедупликации.

    Возвращает:
      - новый список маршрутов;
      - число полностью удалённых маршрутов;
      - число маршрутов, у которых сократили набор направлений.
    """
    removed_dirs_by_route: dict[int, set[int]] = {}

    for uid in removed_uids:
        route_id = meta.get(uid, {}).get("route_id")
        di = meta.get(uid, {}).get("di")

        if route_id is None or di is None:
            continue

        route_id = int(route_id)
        di = int(di)

        if route_id not in removed_dirs_by_route:
            removed_dirs_by_route[route_id] = set()

        removed_dirs_by_route[route_id].add(di)

    new_routes: list[RouteData] = []
    fully_removed = 0
    shortened_routes = 0

    for route in routes:
        removed_dis = removed_dirs_by_route.get(route.route_id)

        if not removed_dis:
            new_routes.append(route)
            continue

        kept_directions = tuple(
            direction
            for di, direction in enumerate(route.directions)
            if di not in removed_dis and len(direction.coords) >= 2
        )

        if not kept_directions:
            fully_removed += 1
            continue

        if len(kept_directions) < len(route.directions):
            shortened_routes += 1

        new_routes.append(
            replace(
                route,
                directions=kept_directions,
            )
        )

    return new_routes, fully_removed, shortened_routes


def _dir_stat_volume_map(
    unit_ids: list[int],
    meta: dict[int, dict[str, Any]],
    dir_stats: dict[tuple[int, int], Any],
    attr: str,
) -> dict[int, float]:
    """Строит карту uid → объём/площадь из статистик по направлениям."""
    result: dict[int, float] = {}

    for uid in unit_ids:
        rec = meta.get(uid, {})

        route_id = rec.get("route_id")
        di = rec.get("di")

        if route_id is None or di is None:
            continue

        stat = dir_stats.get((int(route_id), int(di)))

        if stat is not None:
            result[uid] = float(getattr(stat, attr))

    return result


def _dedup_affected_route_ids(
    removed_uids: set[int],
    meta: dict[int, dict[str, Any]],
) -> set[int]:
    """Маршруты, затронутые удалением направлений (по uid направлений)."""
    affected: set[int] = set()

    for uid in removed_uids:
        route_id = meta.get(uid, {}).get("route_id")

        if route_id is None:
            continue

        try:
            affected.add(int(route_id))
        except TypeError, ValueError:
            continue

    return affected


def _merge_recomputed_stats(
    stats: dict[int, Any],
    dir_stats: dict[tuple[int, int], Any],
    new_stats: dict[int, Any],
    new_dir_stats: dict[tuple[int, int], Any],
    affected_route_ids: set[int],
) -> None:
    """Заменяет статистики затронутых маршрутов; удаляет исчезнувшие."""
    for route_id in list(stats):
        if route_id in affected_route_ids and route_id not in new_stats:
            del stats[route_id]

    stats.update(new_stats)

    for key in list(dir_stats):
        if key[0] in affected_route_ids and key not in new_dir_stats:
            del dir_stats[key]

    dir_stats.update(new_dir_stats)


def _route_inside_bbox(
    route: RouteData,
    bbox: tuple[float, float, float, float] | None,
) -> bool:
    """Проверяет полное попадание всех направлений маршрута в bbox."""
    if route.error or not route.directions or bbox is None:
        return False

    return all(
        direction.coords
        and all(
            math.isfinite(lat)
            and math.isfinite(lon)
            and bbox[0] <= lat <= bbox[2]
            and bbox[1] <= lon <= bbox[3]
            for lat, lon in direction.coords
        )
        for direction in route.directions
    )


# ── Конвейер ─────────────────────────────────────────────────────────────


def run_pipeline(config: CliConfig, ctx: PipelineContext) -> PipelineResult:
    """Выполняет все стадии расчёта и возвращает данные для экспорта."""
    reporter = ctx.reporter
    fetcher = CachedNetworkRouteFetcher(ctx.cache, ctx.sessions)

    try:
        catalog = load_catalog(
            config.catalog_url,
            config.city_input,
            ctx.sessions.get(),
            ctx.cache,
        )
    except Exception as exc:
        raise CatalogLoadError(str(exc)) from exc

    city_slug = catalog.city_slug
    city_title = catalog.city_title

    if not catalog.sections:
        raise ValueError("Поддерживаемые секции не найдены.")

    for section in catalog.sections:
        reporter.line(
            f"  Секция «{section.title}» "
            f"[{type_label(section.route_type)}]: "
            f"{len(section.links)} маршрутов"
        )

    for unrecognized_title in catalog.unrecognized:
        reporter.line(f"  ⚠ Секция «{unrecognized_title}» не распознана — пропущена")

    if config.disabled_types:
        labels = ", ".join(sorted(type_label(t) for t in config.disabled_types))
        reporter.line(f"  Исключённые типы (--no-type): {labels}")

    # ── 1) Базовые типы (по ним строится bbox) ──────────────────────────
    route_tasks_base = build_base_tasks(catalog, city_slug, config)

    if route_tasks_base:
        reporter.line(
            f"\n[2/4] Загрузка базовых маршрутов "
            f"({len(route_tasks_base)} шт., {config.workers} потоков)..."
        )
        base_routes = download_routes(route_tasks_base, config.workers, fetcher)
    else:
        reporter.line("\n[2/4] Базовых маршрутов для загрузки нет.")
        base_routes = []

    bbox = compute_pipeline_bbox(base_routes, config.bbox_buffer)

    if bbox is None:
        reporter.line("  ⚠ bbox не определён: нет валидных базовых маршрутов.")
    else:
        reporter.line(
            f"  bbox по базовым маршрутам: ({bbox[0]:.5f}, {bbox[1]:.5f}) - "
            f"({bbox[2]:.5f}, {bbox[3]:.5f})"
        )

    # ── 2) Вторичные типы ───────────────────────────────────────────────
    route_tasks_secondary = build_secondary_tasks(catalog, city_slug, config)

    if route_tasks_secondary:
        reporter.line(
            f"\n[2.1/4] Загрузка вторичных маршрутов "
            f"({len(route_tasks_secondary)} шт., {config.workers} потоков)..."
        )
        loaded_secondary = download_routes(route_tasks_secondary, config.workers, fetcher)

        if config.no_bbox_filter:
            secondary_routes = list(loaded_secondary)
            reporter.line(
                f"  Вторичных маршрутов: "
                f"{len(secondary_routes)} из {len(loaded_secondary)} "
                f"(bbox-фильтр отключён)"
            )
        elif bbox is None:
            secondary_routes = []
            reporter.line("  ⚠ Вторичные маршруты не добавлены: bbox не определён.")
        else:
            secondary_routes = [
                route for route in loaded_secondary if _route_inside_bbox(route, bbox)
            ]
            reporter.line(
                f"  Вторичных маршрутов после строгого bbox-фильтра: "
                f"{len(secondary_routes)} из {len(loaded_secondary)}"
            )
    else:
        secondary_routes = []
        reporter.line("\n[2.1/4] Вторичных маршрутов для загрузки нет.")

    # Объединяем все загруженные маршруты
    all_routes = base_routes + secondary_routes

    # ── Идеи пассажиров (не фильтруются по bbox) ────────────────────────
    if config.ideas:
        reporter.line("\n[2.2/4] Загрузка идей пассажиров...")

        ideas_data: list[dict[str, Any]] = []

        try:
            ideas_data = load_ideas(
                city_slug=city_slug,
                cache=ctx.cache,
                session_provider=ctx.sessions,
                search=config.ideas_search,
                sort=config.ideas_sort,
                max_pages=config.ideas_max_pages,
            )
        except Exception as exc:
            reporter.line(f"  ⚠ Ошибка загрузки идей: {exc}")

        if config.ideas_search and ideas_data:
            search_terms = [
                term.strip().lower()
                for term in config.ideas_search.split(",")
                if term.strip()
            ]

            before_filter_count = len(ideas_data)

            ideas_data = [
                idea
                for idea in ideas_data
                if any(
                    term in (idea.get("title") or "").lower() for term in search_terms
                )
            ]

            reporter.line(
                f"  Фильтр по заголовку [{', '.join(search_terms)}]: "
                f"отброшено {before_filter_count - len(ideas_data)}, "
                f"осталось {len(ideas_data)}"
            )

        idea_routes = ideas_to_routes(
            ideas_data,
            city_slug,
            ctx.cache,
            ctx.sessions,
            city_title,
        )

        all_routes.extend(idea_routes)

        reporter.line(f"  Добавлено {len(idea_routes)} идей к общему списку")

    # ── Фильтры активных, ошибок, лимиты ────────────────────────────────
    skipped_inactive = 0

    if config.active_only:
        skipped_inactive = sum(1 for route in all_routes if not route.active)
        all_routes = [route for route in all_routes if route.active]

    ok_routes = [route for route in all_routes if not route.error and route.directions]
    bad_routes = [route for route in all_routes if route.error]

    reporter.line(f"  Успешно: {len(ok_routes)} | ошибок: {len(bad_routes)}")

    curvilinearity_limit = config.curv if config.curv is not None else 0.0
    min_length_limit = config.minlen if config.minlen is not None else 0.0
    radius_limit = config.radius if config.radius is not None else 0.0

    center_latitude = config.center_lat
    center_longitude = config.center_lon

    if radius_limit > 0 and (center_latitude is None or center_longitude is None):
        reporter.line(
            "  ⚠ Фильтр по радиусу игнорируется: укажите --center-lat и --center-lon"
        )

        radius_limit = 0.0
        center_latitude = None
        center_longitude = None

    filter_limits = FilterLimits(
        curvilinearity=curvilinearity_limit,
        min_length_km=min_length_limit,
        radius_km=radius_limit,
        center_lat=center_latitude,
        center_lon=center_longitude,
    )

    kept_routes, excluded_routes = apply_route_limits(ok_routes, filter_limits)
    ok_routes = kept_routes

    excluded_counts: dict[str, int] = {}

    for _, reason in excluded_routes:
        excluded_counts[reason] = excluded_counts.get(reason, 0) + 1

    # ── GHS, Overture, POI, дедупликация и т.д. ─────────────────────────
    ghs_stats: dict[int, GhsStats] = {}
    ghs_meta: dict[str, Any] | None = None
    ghs_dir_stats: dict[tuple[int, int], GhsStats] = {}

    if config.ghs:
        reporter.line(
            f"\n[2.4/4] GHS-BUILT-V вдоль маршрутов "
            f"(буфер {config.ghs_buffer:.0f} м)..."
        )

        ghs_stats, ghs_meta, ghs_dir_stats = compute_ghs(
            ok_routes,
            config.ghs_file,
            config.ghs_buffer,
            city_slug,
            ctx.cache,
        )

        if ghs_stats:
            total_volume = sum(stat.volume_m3 for stat in ghs_stats.values())

            reporter.line(
                f"  Рассчитано {len(ghs_stats)} маршрутов, "
                f"суммарно {total_volume / 1e6:.2f} млн м³"
            )

    built_s_stats: dict[int, BuiltSStats] = {}
    built_s_meta: dict[str, Any] | None = None
    built_s_dir_stats: dict[tuple[int, int], BuiltSStats] = {}
    ghs_s_buffer = (
        config.ghs_buffer if config.ghs_s_buffer is None else config.ghs_s_buffer
    )

    if config.ghs_s:
        reporter.line(
            f"\n[2.45/4] GHS-BUILT-S вдоль маршрутов (буфер {ghs_s_buffer:.0f} м)..."
        )

        built_s_stats, built_s_meta, built_s_dir_stats = compute_ghs_s(
            ok_routes,
            config.ghs_s_file,
            ghs_s_buffer,
            city_slug,
            ctx.cache,
            max_val=config.ghs_s_max,
        )

        if built_s_stats:
            total_surface = sum(stat.surface_m2 for stat in built_s_stats.values())

            reporter.line(
                f"  Рассчитано {len(built_s_stats)} маршрутов, "
                f"суммарно {total_surface / 1e6:.2f} млн м²"
            )

    overture_stats: dict[int, OvertureStats] = {}
    overture_meta: dict[str, Any] | None = None
    overture_dir_stats: dict[tuple[int, int], OvertureStats] = {}

    if config.overture and bbox:
        overture_file = config.overture_file

        if overture_file is None:
            reporter.line("\n[3.55/4] Overture Maps: автозагрузка...")

            overture_file = auto_download_overture(
                bbox,
                config.cache_dir,
                theme=config.overture_theme,
                release=config.overture_release,
            )

        if overture_file:
            reporter.line(
                f"\n[3.6/4] Overture: площадь объектов "
                f"(буфер {config.overture_buffer:.0f} м)..."
            )

            overture_stats, overture_meta, overture_dir_stats = compute_overture(
                ok_routes,
                overture_file,
                config.overture_buffer,
                bbox,
                city_slug,
                ctx.cache,
                release=config.overture_release,
            )

            if overture_stats:
                total_overture_area = sum(
                    st.total_area_m2 for st in overture_stats.values()
                )

                reporter.line(
                    f"  Рассчитано {len(overture_stats)} маршрутов, "
                    f"суммарно {total_overture_area / 1e6:.2f} млн м²"
                )
        else:
            reporter.line("  ⚠ Overture: данные не загружены — расчёт пропущен")

    elif config.overture and not bbox:
        reporter.line("  ⚠ Overture: bbox не определён — расчёт пропущен")

    poi_stats: dict[int, PoiStats] = {}
    poi_values: dict[str, float] = {}
    poi_dir_stats: dict[tuple[int, int], PoiStats] = {}

    if config.poi:
        if bbox:
            reporter.line(
                f"\n[3.5/4] POI из OSM вдоль маршрутов "
                f"(буфер {config.poi_buffer:.0f} м)..."
            )

            poi_stats, poi_values, poi_dir_stats = compute_poi(
                ok_routes,
                bbox,
                city_slug,
                config.poi_buffer,
                ctx.cache,
            )

            if poi_stats:
                total_poi_value = sum(
                    poi_stat.total_value for poi_stat in poi_stats.values()
                )

                reporter.line(
                    f"  Рассчитано {len(poi_stats)} маршрутов, "
                    f"суммарное value={total_poi_value:.1f}"
                )
        else:
            reporter.line("  ⚠ POI: bbox не определён — пропущено")

    net_metrics: dict[str, Any] | None = None
    dedup_removed: list[dict[str, Any]] | None = None
    dedup_analysis: dict[str, Any] | None = None
    meta_u: dict[int, dict[str, Any]] = {}

    if config.dedup:
        all_removed: list[dict[str, Any]] = []
        max_passes = max(1, int(config.dedup_passes))

        # Геометрический анализ выполняется один раз: геометрии направлений
        # между проходами не меняются, меняется только активный набор.
        # Раньше dedup_analyze (проекция, буферы, STRtree, все пересечения K2)
        # пересчитывался на каждый проход целиком.
        units = build_units(ok_routes)
        analysis: dict[str, Any] | None = None

        if len(units) >= 2:
            analysis = dedup_analyze(
                ok_routes,
                config.dedup_buffer,
                thr=config.dedup_threshold,
                per_direction=True,
            )

        if analysis:
            dedup_analysis = analysis
            meta_u = analysis["meta"]
            unit_ids = analysis["ids"]

            ghs_volumes_for_dedup: dict[int, float] = {}

            if config.ghs and ghs_dir_stats:
                ghs_volumes_for_dedup = _dir_stat_volume_map(
                    unit_ids, meta_u, ghs_dir_stats, "volume_m3"
                )

            km_before = float(analysis.get("km_coef", 0.0))
            net_km_coef = km_before
            net_total_km = float(analysis.get("total_km", 0.0))
            net_unique_km = float(analysis.get("unique_km", 0.0))
            active_set: set[int] | None = None

            # Расстояния направлений до центра сети считаются один раз —
            # геометрия между проходами не меняется.
            center_distances: dict[int, float] | None = None

            if config.dedup_center_weight > 0.0:
                center_distances = network_center_distances(units)

            for pass_no in range(1, max_passes + 1):
                units = build_units(ok_routes)

                if len(units) < 2:
                    break

                reporter.line(
                    f"\n[3/4] Дедупликация, проход {pass_no}/{max_passes} "
                    f"({len(units)} направлений)..."
                )

                removed, active, residual = dedup_compute_removals(
                    analysis,
                    config.dedup_threshold,
                    ghs_volumes=ghs_volumes_for_dedup,
                    ghs_weight=1.0,
                    initial_active=active_set,
                    center_distances=center_distances,
                    center_weight=config.dedup_center_weight,
                )

                active_set = active

                if not removed:
                    pairs_obj = analysis.get("pairs")

                    pairs_total = 0
                    excess_total = 0

                    if pairs_obj is not None and not getattr(pairs_obj, "empty", True):
                        pairs_total = len(pairs_obj)

                        if "excess" in getattr(pairs_obj, "columns", []):
                            excess_total = int(pairs_obj["excess"].sum())

                    reporter.line(
                        f"  Проход {pass_no}: новых удалений нет "
                        f"(пар найдено: {pairs_total}, "
                        f"избыточных: {excess_total}, "
                        f"остаточных после политики: {len(residual)})"
                    )

                    break

                for rec in removed:
                    rec["проход"] = pass_no

                all_removed.extend(removed)

                removed_uids = {rec["маршрут"] for rec in removed}

                ok_routes, fully_removed, shortened_routes = _apply_dedup_removals(
                    ok_routes,
                    meta_u,
                    removed_uids,
                )

                net_total_km, net_unique_km, km_after = dedup_network_after(
                    analysis, active
                )
                net_km_coef = km_after

                one_direction_routes = sum(
                    1 for route in ok_routes if len(route.directions) == 1
                )

                reporter.line(
                    f"  Проход {pass_no}: удалено направлений {len(removed)} "
                    f"(маршрутов целиком: {fully_removed}, "
                    f"сокращено маршрутов: {shortened_routes}, "
                    f"с одним направлением: {one_direction_routes}), "
                    f"Км: {km_before:.2f} → {km_after:.2f}"
                )

                km_before = km_after

                # Если будет следующий проход, пересчитываем скоринг
                if pass_no < max_passes and ok_routes:
                    if config.ghs:
                        reporter.line(
                            "\n[Пересчёт] GHS-BUILT-V перед следующим "
                            "проходом дедупликации..."
                        )

                        ghs_stats, ghs_meta, ghs_dir_stats = compute_ghs(
                            ok_routes,
                            config.ghs_file,
                            config.ghs_buffer,
                            city_slug,
                            ctx.cache,
                        )

                        ghs_volumes_for_dedup = _dir_stat_volume_map(
                            unit_ids, meta_u, ghs_dir_stats, "volume_m3"
                        )

                    if config.overture and bbox:
                        overture_file = config.overture_file

                        if overture_file is None:
                            overture_file = auto_download_overture(
                                bbox,
                                config.cache_dir,
                                theme=config.overture_theme,
                                release=config.overture_release,
                            )

                        if overture_file:
                            reporter.line(
                                "\n[Пересчёт] Overture перед следующим "
                                "проходом дедупликации..."
                            )

                            overture_stats, overture_meta, overture_dir_stats = (
                                compute_overture(
                                    ok_routes,
                                    overture_file,
                                    config.overture_buffer,
                                    bbox,
                                    city_slug,
                                    ctx.cache,
                                    release=config.overture_release,
                                )
                            )

        if all_removed:
            dedup_removed = [
                {**rec, "шаг": step} for step, rec in enumerate(all_removed, start=1)
            ]

        if dedup_analysis:
            net_metrics = {
                "km_coef": net_km_coef,
                "total_km": net_total_km,
                "unique_km": net_unique_km,
                "epsg": dedup_analysis.get("epsg"),
            }

    if config.dedup and all_removed:
        # Пересчёт после дедупликации выполняется только когда есть удаления;
        # для GHS/GHS-BUILT-S — только по затронутым маршрутам (остальные
        # направления не менялись, их статистики валидны).
        removed_route_ids = _dedup_affected_route_ids(
            {rec["маршрут"] for rec in all_removed},
            meta_u,
        )
        affected_routes = [
            route for route in ok_routes if route.route_id in removed_route_ids
        ]

        if config.ghs:
            reporter.line("\n[Пересчёт] GHS-BUILT-V после дедупликации...")

            if affected_routes:
                new_stats, new_meta, new_dir_stats = compute_ghs(
                    affected_routes,
                    config.ghs_file,
                    config.ghs_buffer,
                    city_slug,
                    ctx.cache,
                )
                _merge_recomputed_stats(
                    ghs_stats,
                    ghs_dir_stats,
                    new_stats,
                    new_dir_stats,
                    removed_route_ids,
                )
                ghs_meta = new_meta
            else:
                _merge_recomputed_stats(
                    ghs_stats,
                    ghs_dir_stats,
                    {},
                    {},
                    removed_route_ids,
                )

            if ghs_stats:
                total_volume = sum(stat.volume_m3 for stat in ghs_stats.values())

                reporter.line(
                    f"  После дедупликации: {len(ghs_stats)} маршрутов, "
                    f"{total_volume / 1e6:.2f} млн м³"
                )

        if config.ghs_s:
            reporter.line("\n[Пересчёт] GHS-BUILT-S после дедупликации...")

            if affected_routes:
                s_new_stats, s_new_meta, s_new_dir_stats = compute_ghs_s(
                    affected_routes,
                    config.ghs_s_file,
                    ghs_s_buffer,
                    city_slug,
                    ctx.cache,
                    max_val=config.ghs_s_max,
                )
                _merge_recomputed_stats(
                    built_s_stats,
                    built_s_dir_stats,
                    s_new_stats,
                    s_new_dir_stats,
                    removed_route_ids,
                )
                built_s_meta = s_new_meta
            else:
                _merge_recomputed_stats(
                    built_s_stats,
                    built_s_dir_stats,
                    {},
                    {},
                    removed_route_ids,
                )

            if built_s_stats:
                total_surface = sum(stat.surface_m2 for stat in built_s_stats.values())

                reporter.line(
                    f"  После дедупликации: {len(built_s_stats)} маршрутов, "
                    f"{total_surface / 1e6:.2f} млн м²"
                )

        # POI: filtered_pois зависит от ВСЕХ маршрутов (коридорный фильтр),
        # поэтому пересчитывается целиком.
        if config.poi and bbox:
            reporter.line("\n[Пересчёт] POI после дедупликации...")

            poi_stats, poi_values, poi_dir_stats = compute_poi(
                ok_routes,
                bbox,
                city_slug,
                config.poi_buffer,
                ctx.cache,
            )

            if poi_stats:
                total_poi_value = sum(stat.total_value for stat in poi_stats.values())

                reporter.line(
                    f"  После дедупликации: {len(poi_stats)} маршрутов, "
                    f"value={total_poi_value:.1f}"
                )

        if config.overture and bbox:
            overture_file = config.overture_file

            if overture_file is None:
                overture_file = auto_download_overture(
                    bbox,
                    config.cache_dir,
                    theme=config.overture_theme,
                    release=config.overture_release,
                )

            if overture_file:
                reporter.line("\n[Пересчёт] Overture после дедупликации...")

                overture_stats, overture_meta, overture_dir_stats = compute_overture(
                    ok_routes,
                    overture_file,
                    config.overture_buffer,
                    bbox,
                    city_slug,
                    ctx.cache,
                    release=config.overture_release,
                )

                if overture_stats:
                    total_overture_area = sum(
                        st.total_area_m2 for st in overture_stats.values()
                    )

                    reporter.line(
                        f"  После дедупликации: {len(overture_stats)} маршрутов, "
                        f"{total_overture_area / 1e6:.2f} млн м²"
                    )

    unique_stops = collect_unique_stops_raw(
        [route for route in ok_routes if route.active]
    )

    generated_routes: list[dict[str, Any]] = []

    if config.generate:
        if not config.ghs_file:
            reporter.line("  ⚠ --generate требует --ghs-file — пропущено")
        elif bbox is None:
            reporter.line("  ⚠ Генерация: bbox не определён — пропущено")
        else:
            if config.gen_population:
                network_area_km2 = _stops_area_km2(unique_stops)

                generation_count = gen_route_count_formula(
                    config.gen_population,
                    network_area_km2,
                    len(unique_stops),
                    config.gen_transfer,
                )

                reporter.line(
                    f"\n[3.7/4] Формула Якимова: "
                    f"N={config.gen_population:.0f} тыс.чел., "
                    f"S={network_area_km2:.0f} км², "
                    f"O={len(unique_stops)} ост., "
                    f"I={config.gen_transfer:.2f} → {generation_count} маршрутов"
                )
            else:
                generation_count = max(1, config.gen_count)

            reporter.line(
                f"\n[3.7/4] Генерация маршрутов "
                f"(Якимов, объём застройки): {generation_count} шт."
            )

            generated_routes = generate_routes_network(
                unique_stops,
                ok_routes,
                config.ghs_file,
                bbox,
                generation_count,
                maxlen_km=config.gen_maxlen,
                corridor_m=config.gen_corridor,
            )

            reporter.line(f"  Сгенерировано маршрутов: {len(generated_routes)}")

    stop_volumes: dict[str, float] | None = None
    heatmap_volumes: dict[str, float] | None = None

    if config.heat_volume:
        if not config.ghs_file:
            reporter.line(
                "  ⚠ --heat-volume требует --ghs-file — объём остановок не рассчитан"
            )
        elif bbox is None:
            reporter.line("  ⚠ Объём остановок: bbox не определён — пропущено")
        else:
            reporter.line(
                f"\n[3.8/4] Объём застройки вокруг остановок "
                f"(радиус {config.heat_vol_radius:.0f} м)..."
            )

            stop_volumes = compute_stop_volumes(
                unique_stops,
                bbox,
                config.ghs_file,
                config.heat_vol_radius,
            )

            if stop_volumes and any(volume > 0 for volume in stop_volumes.values()):
                non_zero_count = sum(
                    1 for volume in stop_volumes.values() if volume > 0
                )

                reporter.line(
                    f"  Рассчитано для {non_zero_count} остановок с застройкой"
                )

                heatmap_volumes = stop_volumes
            else:
                reporter.line(
                    "  ⚠ Объём = 0 для всех остановок — "
                    "тепловая карта по числу маршрутов"
                )

    heatmap: dict[str, Any] | None = None

    if config.heatmap:
        heatmap = build_heatmap(
            unique_stops,
            bbox,
            cell_km=config.heat_cell,
            alpha=config.heat_alpha,
            smooth=config.heat_smooth,
            gamma=config.heat_gamma,
            top_pct=config.heat_top,
            stop_volumes=heatmap_volumes,
            vol_radius=config.heat_vol_radius,
        )

    return PipelineResult(
        city_slug=city_slug,
        city_title=city_title,
        base_task_count=len(route_tasks_base),
        secondary_task_count=len(route_tasks_secondary),
        base_routes=base_routes,
        secondary_routes=secondary_routes,
        all_routes=all_routes,
        ok_routes=ok_routes,
        bad_routes=bad_routes,
        bbox=bbox,
        limits=filter_limits,
        excluded_routes=excluded_routes,
        excluded_counts=excluded_counts,
        skipped_inactive=skipped_inactive,
        ghs_stats=ghs_stats,
        ghs_meta=ghs_meta,
        ghs_dir_stats=ghs_dir_stats,
        built_s_stats=built_s_stats,
        built_s_meta=built_s_meta,
        built_s_dir_stats=built_s_dir_stats,
        overture_stats=overture_stats,
        overture_meta=overture_meta,
        overture_dir_stats=overture_dir_stats,
        poi_stats=poi_stats,
        poi_values=poi_values,
        poi_dir_stats=poi_dir_stats,
        dedup_removed=dedup_removed,
        dedup_analysis=dedup_analysis,
        net_metrics=net_metrics,
        unique_stops=unique_stops,
        generated_routes=generated_routes,
        stop_volumes=stop_volumes,
        heatmap=heatmap,
    )

