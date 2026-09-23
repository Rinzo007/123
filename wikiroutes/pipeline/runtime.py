"""Конвейер расчёта: от каталога до данных для экспорта."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..cache import JsonCache
from ..catalog import load_catalog
from ..config import CliConfig
from ..enums import type_label
from ..errors import CatalogLoadError
from ..filters import compute_bbox
from ..http_client import SessionProvider
from ..metrics import BuiltSStats, GhsStats, OvertureStats, PoiStats
from ..models import BBox, Catalog, FilterLimits, RouteData
from ..od import OdResult, run_od_stage
from ..osm.routes import load_osm_routes, osm_source_bbox
from ..passenger_flow import FlowResult, ModeChoiceConfig, run_passenger_flow
from ..report import Reporter
from ..routes import (
    CachedNetworkRouteFetcher,
    download_routes,
    ensure_reverse_directions,
)
from .dedup_stage import DedupStageResult, run_dedup_stage
from .enrichment_stage import run_enrichment_stage
from .filtering import (
    FilterStageResult,
    report_boundary_area,
    resolve_city_boundary,
    run_filter_stage,
)
from .loading import build_route_tasks, load_idea_routes
from .output_stage import build_pipeline_result
from .spatial_stage import (
    PrecomputedPoi,
    SpatialResult,
    bbox_as_tuple,
    run_spatial_stage,
)

__all__ = ["PipelineContext", "PipelineResult", "run_pipeline"]


@dataclass
class PipelineContext:
    cache: JsonCache
    sessions: SessionProvider
    reporter: Reporter = field(default_factory=Reporter)


@dataclass
class PipelineResult:
    city_slug: str
    city_title: str
    all_routes: list[RouteData]
    ok_routes: list[RouteData]
    bad_routes: list[RouteData]
    limits: FilterLimits
    excluded_routes: list[tuple[RouteData, str]]
    excluded_counts: dict[str, int]
    removed_directions: list[tuple[RouteData, Any, str]]
    skipped_inactive: int
    bbox: BBox | None
    ghs_stats: dict[int, GhsStats]
    ghs_meta: dict[str, Any] | None
    ghs_dir_stats: dict[tuple[int, int], GhsStats]
    built_s_stats: dict[int, BuiltSStats]
    built_s_meta: dict[str, Any] | None
    built_s_dir_stats: dict[tuple[int, int], BuiltSStats]
    overture_stats: dict[int, OvertureStats]
    overture_meta: dict[str, Any] | None
    overture_dir_stats: dict[tuple[int, int], OvertureStats]
    poi_stops_stats: dict[int, PoiStats]
    poi_stops_meta: dict[str, Any] | None
    poi_stops_dir_stats: dict[tuple[int, int], PoiStats]
    dedup_removed: list[dict[str, Any]] | None
    dedup_analysis: dict[str, Any] | None
    net_metrics: dict[str, Any] | None
    dedup_dir_orig: dict[tuple[int, int], int] | None
    unique_stops: dict[str, dict[str, Any]]
    heatmap: dict[str, Any] | None
    boundary_geom: Any | None = None
    boundary_source: str | None = None
    passenger_flow: FlowResult | None = None


def run_pipeline(config: CliConfig, ctx: PipelineContext) -> PipelineResult:
    reporter = ctx.reporter
    fetcher = CachedNetworkRouteFetcher(ctx.cache, ctx.sessions)

    try:
        catalog: Catalog = load_catalog(
            config.catalog_url,
            config.city_input,
            ctx.sessions.get(),
            ctx.cache,
        )
    except Exception as exc:
        raise CatalogLoadError(str(exc)) from exc

    city_slug = catalog.city_slug
    city_title = catalog.city_title
    route_city_slug = config.route_catalog_city or city_slug
    if config.route_catalog_city:
        city_slug = config.city_input
        reporter.line(
            f"  Маршруты привязаны: город {city_slug} ← каталог {route_city_slug}"
        )
    if not catalog.sections:
        raise ValueError("Поддерживаемые секции не найдены.")

    for section in catalog.sections:
        reporter.line(
            f"  Секция «{section.title}» [{type_label(section.route_type)}]: "
            f"{len(section.links)} маршрутов"
        )
    for title in catalog.unrecognized:
        reporter.line(f"  ⚠ Секция «{title}» не распознана — пропущена")
    if config.disabled_types:
        reporter.line(
            "  Исключённые типы (--no-type): "
            + ", ".join(sorted(type_label(t) for t in config.disabled_types))
        )

    # Граница города определяется до загрузки маршрутов: она используется и
    # для bbox OSM-маршрутов, и для фильтрации направлений по границе.
    boundary = resolve_city_boundary(
        config,
        ctx.sessions,
        ctx.cache,
        city_slug,
        reporter,
    )
    if boundary[0] is not None and boundary[1] is not None:
        report_boundary_area(boundary[0], boundary[1], reporter)

    # Матрица корреспонденций строится после дедупликации маршрутов,
    # и пассажиропоток распределяется по уже очищенному набору.

    route_tasks = build_route_tasks(catalog, route_city_slug, config)
    reporter.line(
        f"\n[2/4] Загрузка маршрутов ({len(route_tasks)} шт., {config.workers} потоков)..."
    )
    loaded_routes = (
        download_routes(route_tasks, config.workers, fetcher) if route_tasks else []
    )
    reporter.line(f"  Загружено маршрутов: {len(loaded_routes)}")

    loaded_routes.extend(
        load_idea_routes(
            config,
            city_slug=city_slug,
            cache=ctx.cache,
            session_provider=ctx.sessions,
            reporter=reporter,
        )
    )
    loaded_routes = _load_osm_routes_step(
        config, ctx, city_slug, loaded_routes, boundary_geom=boundary[0]
    )

    # Односторонние маршруты (единственное направление) используют его и как
    # обратное — с развёрнутыми координатами и остановками.
    loaded_routes = ensure_reverse_directions(loaded_routes)

    filter_result: FilterStageResult = run_filter_stage(
        loaded_routes,
        config,
        reporter=reporter,
        sessions=ctx.sessions,
        cache=ctx.cache,
        city_slug=city_slug,
        precomputed_boundary=boundary,
    )
    ok_routes = filter_result.ok_routes

    # Вычисляем bbox для автозагрузки POI (передаётся в обогащение)
    bbox_model = compute_bbox(ok_routes)
    bbox = bbox_as_tuple(bbox_model) if bbox_model else None

    enrichment = run_enrichment_stage(
        routes=ok_routes,
        config=config,
        city_slug=city_slug,
        cache=ctx.cache,
        bbox=bbox,
        reporter=reporter,
    )

    dedup_result: DedupStageResult | None = None
    if config.dedup:
        reporter.line("\n[3/4] Дедупликация...")
        dedup_result = run_dedup_stage(
            routes=ok_routes,
            config=config,
            ghs_dir_stats=enrichment.ghs_dir_stats,
            poi_stops_dir_stats=enrichment.poi_stops_dir_stats,
            reporter=reporter,
        )
        ok_routes = list(dedup_result.routes)
    else:
        reporter.line("\n[3/4] Дедупликация отключена.")

    spatial: SpatialResult = run_spatial_stage(
        routes=ok_routes,
        config=config,
        city_slug=city_slug,
        cache=ctx.cache,
        reporter=reporter,
        existing_poi=PrecomputedPoi(
            stats=enrichment.poi_stops_stats,
            meta=enrichment.poi_stops_meta,
            dir_stats=enrichment.poi_stops_dir_stats,
        ),
    )

    # Матрица корреспонденций строится после дедупликации маршрутов.
    od_result: OdResult | None = None
    if config.od_matrix:
        od_result = run_od_stage(
            boundary=boundary[0],
            config=config,
            session=ctx.sessions.get(),
            cache=ctx.cache,
            city_slug=city_slug,
            reporter=reporter,
        )

    # Пассажиропоток (после фильтрации маршрутов, дедупликации и расчёта OD-матрицы).
    passenger_flow_result: FlowResult | None = None
    if config.passenger_flow and od_result is not None:
        mode_choice = None
        if config.flow_car_mode:
            mode_choice = ModeChoiceConfig(
                car_no_car_share=config.flow_no_car_share,
                car_parking_min=config.flow_car_parking_min,
                car_cost_per_km_eur=config.flow_car_cost_per_km_eur,
                car_parking_eur=config.flow_car_parking_eur,
                car_circuity=config.flow_car_circuity,
                car_speed_kmh=config.flow_car_speed_kmh,
                walk_speed_mps=config.flow_walk_speed_mps,
                walk_circuity=config.flow_walk_circuity,
                vot_per_eur_s=config.flow_vot_per_eur_s,
                fare_base_eur=config.flow_fare_base_eur,
                fare_per_km_eur=config.flow_fare_per_km_eur,
                fare_cap_eur=config.flow_fare_cap_eur,
                min_fare_eur=config.flow_fare_min_eur,
                two_wheel_share=config.flow_two_wheel_share,
                two_wheel_speed_mps=config.flow_two_wheel_speed_mps,
                two_wheel_reach_m=config.flow_two_wheel_reach_m,
                two_wheel_per_km_eur=config.flow_two_wheel_per_km_eur,
            )
        periods = config.flow_periods or od_result.purpose_periods
        passenger_flow_result = run_passenger_flow(
            routes=ok_routes,
            od_matrix=od_result.matrix,
            od_sparse=od_result.sparse_matrix,
            zones=od_result.zones,
            stop_time_min=config.flow_stop_time_min,
            logit_temp=config.flow_logit_temp,
            stop_search_radius_m=config.flow_stop_search_radius_m,
            wait_time_min=config.flow_wait_time_min,
            walk_to_stop_min=config.flow_walk_to_stop_min,
            transfer_penalty_min=config.flow_transfer_penalty_min,
            transfer_penalty_calc=config.flow_transfer_penalty_calc,
            transfer_radius_m=config.flow_transfer_radius_m,
            max_transfers=config.flow_max_transfers,
            headway_min=config.flow_headway_min,
            wait_crowding_per_100_min=config.flow_wait_crowding_per_100_min,
            transfer_wait_min=config.flow_transfer_wait_min,
            periods=periods,
            mode_choice=mode_choice,
            capex_factor=config.flow_capex_factor,
            reporter=reporter,
            wait_calc=config.flow_wait_calc,
            include_reliability=config.flow_include_reliability,
            msa_max_iterations=(
                config.flow_msa_max_iterations
                if config.flow_msa_max_iterations > 0
                else None
            ),
            msa_gap=config.flow_msa_gap,
        )

    return build_pipeline_result(
        city_slug=city_slug,
        city_title=city_title,
        all_routes=loaded_routes,
        filter_result=filter_result,
        enrichment=enrichment,
        dedup_result=dedup_result,
        spatial=spatial,
        ok_routes=ok_routes,
        passenger_flow=passenger_flow_result,
    )


def _load_osm_routes_step(
    config: CliConfig,
    ctx: PipelineContext,
    city_slug: str,
    loaded_routes: list[RouteData],
    boundary_geom: Any | None = None,
) -> list[RouteData]:
    """Выполняет подмешивание маршрутов ОТ из OSM (по bbox границы города)."""
    if not config.osm_routes:
        return loaded_routes
    reporter = ctx.reporter
    reporter.line("\n[2.5/4] Добавление маршрутов ОТ из OSM...")

    bbox_model = compute_bbox(loaded_routes)
    fallback = bbox_as_tuple(bbox_model) if bbox_model else None
    bbox = osm_source_bbox(
        city_slug,
        cache=ctx.cache,
        sessions=ctx.sessions,
        config=config,
        fallback_bbox=fallback,
        boundary_geom=boundary_geom,
    )
    osm_routes = load_osm_routes(
        city_slug,
        cache=ctx.cache,
        sessions=ctx.sessions,
        reporter=reporter,
        bbox=bbox,
        config=config,
        type_filter=config.type_filter,
        disabled_types=config.disabled_types,
    )
    loaded_routes.extend(osm_routes)
    return loaded_routes
