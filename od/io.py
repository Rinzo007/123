"""Сохранение/загрузка OD-результатов, кэш генерации и стадия OD.

Запись матриц/пар/зон в parquet (CSV-фолбэк), распределение поездок на
рёбра сети (All-or-Nothing), атомарный кэш сгенерированной матрицы и
оркестрация ``run_od_stage``: внешний спрос Takt, файлы внешней модели,
расчёт на лету по границе города.
"""

from __future__ import annotations

import itertools
import json
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import networkx as nx
import numpy as np
from shapely.geometry import box
from shapely.geometry import mapping as shapely_mapping

from ..cache import JsonCache
from .builders import (
    build_gravity_od,
    build_purpose_od,
    periods_from_purpose_blend,
)
from .demand import (
    _write_demand_street_geojson,
    load_demand_streets,
    load_takt_demand,
    zone_weights_from_streets,
)
from .model import OdMatrixError, OdResult, Zones, _as_sparse
from .network import (
    build_road_graph,
    euclidean_costs,
    fetch_road_ways,
    zone_network_costs,
)
from .zones import (
    assign_district_names,
    build_zones,
    load_districts,
    load_zones_from_file,
    zonal_weights,
)

__all__ = [
    "assign_road_loads",
    "load_od_from_files",
    "run_od_stage",
    "save_od_outputs",
    "save_road_load_outputs",
]

_OD_GENERATED_KIND = "od_generated"
# Версия формата кэша: при изменении структуры/формул пересчёт заново.
_OD_CACHE_SCHEMA_VERSION = 3


def _write_frame(frame: Any, path: Path) -> Path:
    """Пишет DataFrame в parquet, а при отсутствии pyarrow — в CSV."""
    try:
        frame.to_parquet(path)
        return path
    except Exception:  # noqa: BLE001 — fallback без тяжёлой зависимости
        csv_path = path.with_suffix(".csv")
        frame.to_csv(csv_path, index=False, encoding="utf-8-sig")
        return csv_path


def _read_frame(path: str | Path) -> Any:
    """Читает DataFrame из parquet или CSV (обратный `_write_frame`)."""
    import pandas as pd

    if Path(path).suffix.lower() == ".csv":
        return pd.read_csv(path)
    return pd.read_parquet(path)


def _write_zones_geojson(
    zones: Zones, path: Path, district_names: Sequence[str] | None = None
) -> None:
    features = [
        {
            "type": "Feature",
            "properties": {
                "zone_id": int(zones.ids[i]),
                **({"district": district_names[i]} if district_names else {}),
            },
            "geometry": shapely_mapping(zones.polygons[i]),
        }
        for i in range(len(zones))
    ]
    path.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}),
        encoding="utf-8",
    )


def save_od_outputs(
    zones: Zones,
    matrix: np.ndarray,
    *,
    city: str,
    out_dir: str | Path | None = None,
    production: np.ndarray | None = None,
    attraction: np.ndarray | None = None,
    purpose_trips: Sequence[float] | None = None,
    purpose_keys: Sequence[str] | None = None,
    district_names: Sequence[str] | None = None,
    write_matrix_frame: bool = True,
) -> list[Path]:
    """Сохраняет матрицу, пары и зоны; возвращает записанные пути.

    Колонки ``production``/``attraction`` (если заданы) попадают в файл зон
    в формате Tranmodel, чтобы результат можно было перечитать через
    ``load_zones_from_file``/``load_od_from_files``. При заданных
    ``purpose_trips``/``purpose_keys`` пишется разбиение по целям поездок,
    при ``district_names`` — названия районов для каждой зоны.
    ``write_matrix_frame=False`` пропускает плотный ``od_matrix.parquet``
    (для больших городов) — матрица восстанавливается из ``od_pairs``.
    """
    import geopandas as gpd
    import pandas as pd

    out = Path(out_dir or ".")
    out.mkdir(parents=True, exist_ok=True)
    ids = zones.ids.tolist()
    written: list[Path] = []
    if write_matrix_frame:
        frames = pd.DataFrame(matrix, index=ids, columns=ids)
        written.append(
            _write_frame(frames, out / f"{city}_od_matrix.parquet")
        )
    rows, cols = np.nonzero(matrix > 0)
    ids_arr = np.asarray(ids, dtype=np.int64)
    pairs = pd.DataFrame(
        {
            "orig_zone": ids_arr[rows],
            "dest_zone": ids_arr[cols],
            "trips": matrix[rows, cols],
        }
    )
    pairs = pairs[pairs["orig_zone"] != pairs["dest_zone"]]
    pairs = pairs.sort_values("trips", ascending=False)
    written.append(_write_frame(pairs, out / f"{city}_od_pairs.parquet"))
    if purpose_trips is not None:
        keys = list(purpose_keys or ())
        total = max(float(sum(purpose_trips)), 1.0)
        purpose_frame = pd.DataFrame(
            {
                "purpose": keys,
                "trips": [float(v) for v in purpose_trips],
                "share": [float(v) / total for v in purpose_trips],
            }
        )
        written.append(_write_frame(purpose_frame, out / f"{city}_od_purposes.parquet"))
    zones_path = out / f"{city}_od_zones.geojson"
    _write_zones_geojson(zones, zones_path, district_names=district_names)
    written.append(zones_path)
    zdf = gpd.GeoDataFrame(
        {"zone_id": zones.ids, "geometry": list(zones.polygons)},
        crs="EPSG:4326",
    )
    if production is not None:
        zdf["production"] = np.asarray(production)
    if attraction is not None:
        zdf["attraction"] = np.asarray(attraction)
    if district_names is not None:
        zdf["district"] = list(district_names)
    written.append(_write_frame(zdf, out / f"{city}_od_zones.parquet"))
    return written


def assign_road_loads(
    zones: Zones,
    matrix: np.ndarray,
    graph: nx.Graph,
    reporter: Any | None = None,
) -> dict[tuple[Any, Any], float]:
    """Распределяет поездки OD на рёбра сети (All-or-Nothing).

    Каждая пара ``(i, j)`` направляется по кратчайшему (во времени) пути
    между центрами зон; поток добавляется на все рёбра этого пути.
    Возвращает ``{упорядоченная пара узлов: поездок}``. Пары без пути
    (разрывы сети) не присваиваются; при заданном ``reporter`` выводится
    предупреждение с числом нераспределённых поездок.
    """
    from .network import _snapped_centroids

    snapped = _snapped_centroids(zones, graph)
    loaded: dict[tuple[Any, Any], float] = {}
    # Один Dijkstra на уникальный узел привязки вместо одного на зону.
    grouped: dict[Any, list[int]] = {}
    for i, source in enumerate(snapped):
        grouped.setdefault(source, []).append(i)
    total_trips = 0.0
    unassigned_trips = 0.0
    for source, indices in grouped.items():
        _, paths = nx.single_source_dijkstra(graph, source, weight="time")
        for i in indices:
            marker_rows = np.nonzero(matrix[i])[0] if matrix.shape[0] else ()
            for j in marker_rows:
                if i == j:
                    continue
                trips = float(matrix[i, j])
                if trips <= 0.0:
                    continue
                total_trips += trips
                nodes = paths.get(snapped[j])
                if not nodes:
                    unassigned_trips += trips
                    continue
                for a, b in itertools.pairwise(nodes):
                    edge = (a, b) if a < b else (b, a)
                    loaded[edge] = loaded.get(edge, 0.0) + trips
    if reporter is not None and unassigned_trips > 0.0:
        share = unassigned_trips / max(total_trips, 1e-9) * 100.0
        reporter.line(
            f"  Не распределено на сеть: {unassigned_trips:,.0f} поездок "
            f"({share:.1f}%) — проверьте связность дорожной сети"
        )
    return loaded


def save_road_load_outputs(
    loads: dict[tuple[Any, Any], float],
    *,
    city: str,
    out_dir: str | Path | None = None,
) -> list[Path]:
    """Сохраняет загрузку рёбер (pairs + geojson); возвращает записанные пути."""
    import pandas as pd

    out = Path(out_dir or ".")
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    if not loads:
        return written
    edges = sorted(loads.keys(), key=lambda e: (-loads[e], e))
    frame = pd.DataFrame(
        {
            "node_a": [a for a, _ in edges],
            "node_b": [b for _, b in edges],
            "trips": [loads[e] for e in edges],
        }
    )
    written.append(_write_frame(frame, out / f"{city}_od_road_loads.parquet"))
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"trips": float(loads[e])},
                "geometry": {
                    "type": "LineString",
                    "coordinates": [list(e[0]), list(e[1])],
                },
            }
            for e in edges
        ],
    }
    path = out / f"{city}_od_road_loads.geojson"
    path.write_text(json.dumps(geojson, ensure_ascii=False, indent=1), encoding="utf-8")
    written.append(path)
    return written


def load_od_from_files(
    matrix_path: str | Path,
    zones_path: str | Path,
    *,
    reporter: Any = None,
) -> OdResult:
    """Загружает готовую OD-матрицу и зоны из файлов (собранные внешней
    транспортной моделью), минуя расчёт на лету.

    Ожидаемый формат: ``od_matrix.parquet`` (строки/столбцы — идентификаторы
    зон) и ``zones.parquet`` (GeoDataFrame с колонкой ``zone_id`` и геометрией).
    Если ``od_matrix.parquet`` отсутствует, матрица восстанавливается из
    ``od_pairs.parquet`` (``orig_zone``/``dest_zone``/``trips``) — формат,
    получаемый при ``write_matrix_frame=False``.
    Геометрия зон перепроецируется в EPSG:4326, матрица выравнивается по
    порядку ``zone_id`` из файла зон.
    """
    import pandas as pd

    line = getattr(reporter, "line", None)
    if line:
        line(f"  Загрузка OD из файлов: {matrix_path}, {zones_path}")
    zones, weights, _attraction = load_zones_from_file(zones_path, reporter=None)
    ids = zones.ids
    ids_list = [str(int(v)) for v in ids]  # parquet может хранить id как строки
    matrix_path = Path(matrix_path)
    if matrix_path.exists():
        probe = _read_frame(matrix_path)
        if {"orig_zone", "dest_zone", "trips"}.issubset(probe.columns):
            # Файл пар: плотной матрицы нет — восстанавливаем из пар.
            mdf = _frame_from_pairs(matrix_path, ids_list)
        else:
            mdf = probe
            mdf.columns = [str(c) for c in mdf.columns]
            if not isinstance(mdf.index, pd.RangeIndex) or len(mdf) != len(ids):
                mdf.index = [str(v) for v in mdf.index]
                mdf = mdf.reindex(index=ids_list, columns=ids_list)
    else:
        if line:
            line("  Плотная матрица не найдена: восстанавливаю из OD-пар")
        mdf = _frame_from_pairs(matrix_path, ids_list)
    matrix = mdf.to_numpy(dtype=np.float64)
    if matrix.shape != (len(ids), len(ids)):
        raise OdMatrixError(
            f"Несовпадение размеров: зон в файле {len(ids)}, матрица {matrix.shape}"
        )
    if not np.isfinite(matrix).all():
        raise OdMatrixError(
            "Матрица OD не покрывает все зоны: есть NaN после выравнивания по zone_id"
        )
    n = len(ids)
    if line:
        line(
            f"  Зон: {n:,}, поездок: {float(matrix.sum()):,.0f}, "
            f"OD-пар: {int((matrix > 0).sum()):,}"
        )
    return OdResult(
        zones=zones,
        matrix=matrix,
        costs=np.zeros((n, n), dtype=np.float64),
        weights=weights,
        road_ways=0,
        euclidean=True,
        road_loads=None,
        sparse_matrix=_as_sparse(matrix),
    )


def _frame_from_pairs(
    pairs_path: str | Path, ids: Sequence[str]
) -> Any:
    """Собирает квадратную матрицу по зонам из OD-пар (без плотного parquet)."""
    import pandas as pd

    df = _read_frame(pairs_path)
    if not {"orig_zone", "dest_zone", "trips"}.issubset(df.columns):
        raise OdMatrixError(
            f"{pairs_path}: ожидаются колонки orig_zone/dest_zone/trips"
        )
    frame = pd.DataFrame(0.0, index=ids, columns=ids, dtype=float)
    keep = df["orig_zone"].astype(str).isin(ids) & df["dest_zone"].astype(str).isin(ids)
    sub = df.loc[keep, ["orig_zone", "dest_zone", "trips"]]
    if sub.empty:
        return frame
    pv = sub.pivot_table(
        index="orig_zone", columns="dest_zone", values="trips", aggfunc="sum"
    ).astype(float)
    pv = pv.fillna(0.0)  # пара даёт нуль на диагонали и в отсечённых клетках
    frame.loc[pv.index.astype(str), pv.columns.astype(str)] = pv.to_numpy()
    return frame


def _od_generated_cache_dir(
    cache: JsonCache | None,
    *,
    city_slug: str,
    boundary: Any,
    config: Any,
    zones_file: str | Path | None,
) -> Path | None:
    """Определяет каталог кэша сгенерированной OD-матрицы (или None)."""
    import hashlib

    if cache is None:
        return None
    weight_paths = (config.ghs_file, config.ghs_s_file)
    if not any(weight_paths):
        from ..config import DEFAULT_GHS_FILE

        candidate = str(DEFAULT_GHS_FILE) if DEFAULT_GHS_FILE else None
        if candidate:
            weight_paths = (candidate, None)
    parts = [
        city_slug,
        f"v{_OD_CACHE_SCHEMA_VERSION}",
        f"zm{float(config.od_zone_size_m):.3f}",
        f"decay{getattr(config, 'od_decay_minutes', None)!s}",
        f"purposes{int(bool(getattr(config, 'od_purposes', False)))}",
        str(zones_file or ""),
        "|".join(str(p or "") for p in weight_paths),
    ]
    if boundary is not None:
        parts.append(str(boundary.wkt if hasattr(boundary, "wkt") else boundary))
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
    return Path(cache.root) / _OD_GENERATED_KIND / f"{city_slug}_{digest}"


def _save_generated_od(
    cache_dir: Path,
    *,
    zones: Zones,
    matrix: np.ndarray,
    costs: np.ndarray,
    production: np.ndarray,
    attraction: np.ndarray,
    road_ways: int,
    euclidean: bool,
    purpose_meta: dict[str, Any] | None = None,
    district_names: Sequence[str] | None = None,
) -> None:
    """Сохраняет результат генерации в кэш (parquet + npy + meta).

    Пишет в одноразовый каталог-сосед и атомарно переименовывает, чтобы
    частично записанный кэш не читался как готовый.
    """
    import os
    import shutil

    import geopandas as gpd
    import pandas as pd

    parent = cache_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f"{cache_dir.name}.", dir=parent))
    try:
        zdf = gpd.GeoDataFrame(
            {"zone_id": zones.ids, "geometry": list(zones.polygons)},
            crs="EPSG:4326",
        )
        zdf["production"] = np.asarray(production, dtype=np.float64)
        zdf["attraction"] = np.asarray(attraction, dtype=np.float64)
        if district_names is not None:
            zdf["district"] = list(district_names)
        _write_frame(zdf, tmp / "zones.parquet")
        ids = zones.ids.tolist()
        _write_frame(
            pd.DataFrame(matrix, index=ids, columns=ids),
            tmp / "matrix.parquet",
        )
        np.save(tmp / "costs.npy", costs)
        meta: dict[str, Any] = {
            "road_ways": int(road_ways),
            "euclidean": bool(euclidean),
            "schema_version": _OD_CACHE_SCHEMA_VERSION,
        }
        if purpose_meta:
            meta["purposes"] = purpose_meta
        if district_names is not None:
            meta["districts"] = list(district_names)
        (tmp / "meta.json").write_text(
            json.dumps(meta),
            encoding="utf-8",
        )
        if cache_dir.exists():
            shutil.rmtree(cache_dir, ignore_errors=True)
        os.replace(tmp, cache_dir)
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)


def _load_generated_od(
    cache_dir: Path,
    *,
    reporter: Any = None,
) -> OdResult | None:
    """Загружает OD-результат из кэша; None при повреждении/отсутствии.

    Учитывает CSV-фолбэк ``_write_frame`` (``*.parquet`` или ``*.csv``)
    и несовпадение версии формата (``schema_version``).
    """
    line = getattr(reporter, "line", None)

    def _any(*names: str) -> Path | None:
        for name in names:
            path = cache_dir / name
            if path.is_file():
                return path
        return None

    matrix_path = _any("matrix.parquet", "matrix.csv")
    zones_path = _any("zones.parquet", "zones.csv")
    costs_path = _any("costs.npy")
    meta_path = _any("meta.json")
    if not all((matrix_path, zones_path, costs_path, meta_path)):
        return None
    try:
        zones, production, _attraction = load_zones_from_file(
            zones_path, reporter=None
        )
        ids = zones.ids
        ids_list = [str(int(v)) for v in ids]
        mdf = _read_frame(matrix_path)
        mdf.index = [str(v) for v in mdf.index]
        mdf.columns = [str(c) for c in mdf.columns]
        mdf = mdf.reindex(index=ids_list, columns=ids_list)
        matrix = mdf.to_numpy(dtype=np.float64)
        costs = np.load(costs_path, allow_pickle=False)
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("schema_version") != _OD_CACHE_SCHEMA_VERSION:
            if line:
                line(
                    f"  Кэш OD устарел (версия {meta.get('schema_version')}), "
                    "пересчёт..."
                )
            return None
        purpose_periods: tuple[Any, ...] = ()
        purpose_trips: tuple[float, ...] = ()
        purpose_keys: tuple[str, ...] = ()
        purpose_meta = meta.get("purposes")
        if isinstance(purpose_meta, dict) and purpose_meta.get("out") and purpose_meta.get("ret"):
            purpose_periods = periods_from_purpose_blend(
                purpose_meta["out"], purpose_meta["ret"]
            )
            purpose_trips = tuple(float(v) for v in purpose_meta.get("totals", ()))
            purpose_keys = tuple(str(v) for v in purpose_meta.get("keys", ()))
        district_names: tuple[str, ...] = tuple(
            str(v) for v in meta.get("districts", ())
        )
        if len(district_names) != len(zones):
            district_names = ()
    except Exception as exc:  # noqa: BLE001 — повреждённый кэш не критичен
        if line:
            line(f"  Кэш OD повреждён, пересчёт: {exc}")
        return None
    return OdResult(
        zones=zones,
        matrix=matrix,
        costs=costs,
        weights=production,
        road_ways=int(meta.get("road_ways", 0)),
        euclidean=bool(meta.get("euclidean", False)),
        sparse_matrix=_as_sparse(matrix),
        purpose_periods=purpose_periods,
        purpose_trips=purpose_trips,
        purpose_keys=purpose_keys,
        district_names=district_names,
    )


def _prepare_generation(
    boundary: Any,
    config: Any,
    reporter: Any,
) -> tuple[Zones, np.ndarray, np.ndarray, np.ndarray, Any]:
    """Подготавливает зоны и веса для генерации OD.

    Возвращает ``(zones, production, attraction, weights, geo_boundary)``:
    зоны берутся из файла Tranmodel (``od_zones_file``) либо строятся по
    границе города; geo_boundary — область загрузки дорог (граница или bbox
    зон).
    """
    if getattr(config, "od_zones_file", None):
        zones, production, attraction = load_zones_from_file(
            config.od_zones_file, reporter=reporter
        )
        weights = production
        bbox = box(*zones.bounds)
        geo_boundary = boundary if boundary is not None else bbox
    else:
        if boundary is None:
            raise OdMatrixError(
                "Нет границы города и не задан --od-zones-file: "
                "не из чего строить зоны OD-матрицы"
            )
        zones = build_zones(boundary, size_m=config.od_zone_size_m)
        weight_path = next((p for p in (config.ghs_file, config.ghs_s_file) if p), None)
        if not weight_path:
            from ..config import DEFAULT_GHS_FILE

            candidate = str(DEFAULT_GHS_FILE) if DEFAULT_GHS_FILE else None
            if candidate:
                weight_path = candidate
        production = zonal_weights(zones, weight_path)
        attraction = production.copy()
        weights = production
        geo_boundary = boundary
    return zones, production, attraction, weights, geo_boundary


def run_od_stage(
    *,
    boundary: Any,
    config: Any,
    session: Any,
    cache: JsonCache,
    city_slug: str,
    reporter: Any,
    out_dir: str | Path | None = None,
) -> OdResult | None:
    """Выполняет стадию OD-матрицы; None, если стадия отключена."""
    if not config.od_matrix:
        return None
    reporter.line("\n[OD] Матрица корреспонденций...")
    external_matrix = getattr(config, "od_matrix_file", None)
    if external_matrix:
        zones_file = getattr(config, "od_zones_file", None)
        if not zones_file:
            raise OdMatrixError(
                "Задана готовая матрица --od-matrix-file, но не задан "
                "--od-zones-file: зоны нужны для выравнивания матрицы"
            )
        return load_od_from_files(
            external_matrix,
            zones_file,
            reporter=reporter,
        )
    demand_file = getattr(config, "od_demand_file", None)
    if demand_file:
        demand = load_takt_demand(demand_file)
        district_names: tuple[str, ...] = ()
        districts_file = getattr(config, "od_districts_file", None)
        if districts_file:
            district_names = assign_district_names(
                demand.zones, load_districts(districts_file)
            )
        total_trips = float(demand.matrix.sum())
        n_pairs = int((demand.matrix > 0).sum())
        reporter.line(
            f"  Спрос Takt: зон {len(demand.zones):,}, поездок "
            f"{total_trips:,.0f}, OD-пар {n_pairs:,} ({Path(demand_file).name})"
        )
        save_od_outputs(
            demand.zones,
            demand.matrix,
            city=city_slug,
            out_dir=out_dir,
            production=demand.production,
            attraction=demand.production,
            district_names=district_names or None,
        )
        return OdResult(
            zones=demand.zones,
            matrix=demand.matrix,
            costs=euclidean_costs(demand.zones),
            weights=demand.production,
            road_ways=0,
            euclidean=True,
            sparse_matrix=_as_sparse(demand.matrix),
            district_names=district_names,
        )
    zones, production, attraction, weights, geo_boundary = _prepare_generation(
        boundary, config, reporter
    )
    weights_file = getattr(config, "od_weights_file", None)
    if weights_file:
        street_edges = load_demand_streets(weights_file)
        production = zone_weights_from_streets(zones, street_edges)
        attraction = production.copy()
        weights = production
        if out_dir is not None:
            _write_demand_street_geojson(
                zones, street_edges, Path(out_dir) / f"{city_slug}_od_street_demand.geojson"
            )
        reporter.line(
            f"  Веса зон: по рёбрам спроса {Path(weights_file).name} "
            f"({len(street_edges):,} рёбер)"
        )
    district_names: tuple[str, ...] = ()
    districts_file = getattr(config, "od_districts_file", None)
    if districts_file:
        places = load_districts(districts_file)
        district_names = assign_district_names(zones, places)
    cache_dir = _od_generated_cache_dir(
        cache,
        city_slug=city_slug,
        boundary=geo_boundary,
        config=config,
        zones_file=getattr(config, "od_zones_file", None),
    )
    if cache_dir is not None and getattr(cache, "read_enabled", True):
        cached = _load_generated_od(cache_dir, reporter=reporter)
        if cached is not None:
            save_od_outputs(
                cached.zones,
                cached.matrix,
                city=city_slug,
                out_dir=out_dir,
                production=production,
                attraction=attraction,
                purpose_trips=cached.purpose_trips or None,
                purpose_keys=cached.purpose_keys or None,
                district_names=(cached.district_names or None)
                if districts_file
                else None,
            )
            n_pairs = int((cached.matrix > 0).sum())
            reporter.line(
                f"  Зон: {len(cached.zones)}, поездок: {float(cached.matrix.sum()):,.0f}, "
                f"OD-пар: {n_pairs:,} (из кэша)"
            )
            return cached
    ways = fetch_road_ways(geo_boundary, session, cache, cache_key=city_slug, config=config)
    euclidean = ways is None
    if euclidean:
        reporter.line("  Дорожная сеть недоступна — время по прямой")
        costs = euclidean_costs(zones)
    else:
        costs = zone_network_costs(zones, build_road_graph(ways))
    purpose_meta: dict[str, Any] | None = None
    purpose_periods: tuple[Any, ...] = ()
    purpose_trips: list[float] | None = None
    purpose_keys: list[str] | None = None
    if getattr(config, "od_purposes", False):
        purpose_od = build_purpose_od(
            production,
            zones,
            engine=getattr(config, "od_purpose_engine", "takt"),
        )
        matrix = purpose_od.matrix
        purpose_periods = periods_from_purpose_blend(
            purpose_od.period_out, purpose_od.period_ret
        )
        purpose_trips = [float(m.sum()) for m in purpose_od.purpose_matrices]
        purpose_keys = [p.key for p in purpose_od.purposes]
        purpose_meta = {
            "out": list(purpose_od.period_out),
            "ret": list(purpose_od.period_ret),
            "totals": purpose_trips,
            "keys": purpose_keys,
        }
        reporter.line(
            "  Цели поездок: од по тяготению по целям "
            + ", ".join(f"{k}={v:,.0f}" for k, v in zip(purpose_keys, purpose_trips))
        )
    else:
        matrix = build_gravity_od(
            production,
            costs,
            attraction=attraction,
            decay_minutes=getattr(config, "od_decay_minutes", None),
        )
    if cache_dir is not None and getattr(cache, "write_enabled", True):
        _save_generated_od(
            cache_dir,
            zones=zones,
            matrix=matrix,
            costs=costs,
            production=production,
            attraction=attraction,
            road_ways=len(ways or []),
            euclidean=euclidean,
            purpose_meta=purpose_meta,
            district_names=district_names if districts_file else None,
        )
    save_od_outputs(
        zones,
        matrix,
        city=city_slug,
        out_dir=out_dir,
        production=production,
        attraction=attraction,
        purpose_trips=purpose_trips,
        purpose_keys=purpose_keys,
        district_names=district_names if districts_file else None,
    )
    total = max(float(matrix.sum()), 1.0)
    ordered = np.sort(matrix, axis=None)
    compact = ordered[ordered > 0]
    avg_time = (
        float((matrix * np.where(np.isfinite(costs), costs, 0.0)).sum() / total)
        if compact.size > 1
        else 0.0
    )
    n_pairs = int((matrix > 0).sum())
    reporter.line(
        f"  Зон: {len(zones)}, поездок: {float(matrix.sum()):,.0f}, "
        f"OD-пар: {n_pairs:,}, среднее время: {avg_time:.1f} мин "
    )
    return OdResult(
        zones=zones,
        matrix=matrix,
        costs=costs,
        weights=weights,
        road_ways=len(ways or []),
        euclidean=euclidean,
        sparse_matrix=_as_sparse(matrix),
        purpose_periods=purpose_periods,
        district_names=district_names if districts_file else (),
    )