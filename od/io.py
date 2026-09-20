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
    build_purpose_od,
    build_takt_demand_with_purposes,
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
from .model import OdMatrixError, OdResult, Zones, _as_sparse
from ..passenger_flow.models import PURPOSE_DEFAULTS
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
_OD_CACHE_SCHEMA_VERSION = 4
# Плотный фрейм матрицы пишется целиком, пока клеток не больше порога;
# для больших сеток сохраняются только ненулевые пары (od_pairs — внешний
# формат с id зон; matrix_pairs — внутренний формат кэша с позициями).
_OD_DENSE_FRAME_CELLS = 3_000_000

# Колонки пары-форматов: позиционный (внутренний кэш) и по id зон (внешний).
_PAIRS_POS_COLUMNS = ("oi", "di", "trips")
_PAIRS_ID_COLUMNS = ("orig_zone", "dest_zone", "trips")


def _dense_frame_wanted(zones: Zones) -> bool:
    """Достаточно ли мала сетка, чтобы писать плотную матрицу целиком."""
    return len(zones) ** 2 <= _OD_DENSE_FRAME_CELLS


def _pairs_frame(
    matrix: np.ndarray,
    ids: Sequence[int | float | str],
    *,
    positions: bool = False,
) -> Any:
    """DataFrame ненулевых OD-пар без диагонали.

    ``positions=False``: колонки ``orig_zone/dest_zone/trips`` с
    идентификаторами зон (внешний формат ``od_pairs.parquet``).
    ``positions=True``: колонки ``oi/di/trips`` с целочисленными позициями
    зон 0..N-1 (внутренний формат кэша ``matrix_pairs.parquet`` —
    реконструкция матрицы без строкового маппинга).
    """
    import pandas as pd

    rows, cols = np.nonzero(matrix > 0)
    keep = rows != cols
    rows, cols = rows[keep], cols[keep]
    if positions:
        return pd.DataFrame(
            {
                _PAIRS_POS_COLUMNS[0]: rows.astype(np.int64),
                _PAIRS_POS_COLUMNS[1]: cols.astype(np.int64),
                _PAIRS_POS_COLUMNS[2]: matrix[rows, cols],
            }
        )
    ids_arr = np.asarray(list(ids), dtype=np.int64)
    return pd.DataFrame(
        {
            _PAIRS_ID_COLUMNS[0]: ids_arr[rows],
            _PAIRS_ID_COLUMNS[1]: ids_arr[cols],
            _PAIRS_ID_COLUMNS[2]: matrix[rows, cols],
        }
    )


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
    pairs = _pairs_frame(matrix, ids)
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
        source_total, source_unassigned = _assign_source_flow(
            graph, source, indices, matrix, snapped, loaded
        )
        total_trips += source_total
        unassigned_trips += source_unassigned
    if reporter is not None and unassigned_trips > 0.0:
        share = unassigned_trips / max(total_trips, 1e-9) * 100.0
        reporter.line(
            f"  Не распределено на сеть: {unassigned_trips:,.0f} поездок "
            f"({share:.1f}%) — проверьте связность дорожной сети"
        )
    return loaded


def _assign_source_flow(
    graph: nx.Graph,
    source: Any,
    indices: Sequence[int],
    matrix: np.ndarray,
    snapped: Sequence[Any],
    loaded: dict[tuple[Any, Any], float],
) -> tuple[float, float]:
    """Направляет поездки всех зон, привязанных к ``source``.

    Возвращает ``(поездок назначено, поездок без пути)`` для узла-источника.
    """
    _, paths = nx.single_source_dijkstra(graph, source, weight="time")
    total_trips = 0.0
    unassigned_trips = 0.0
    for i in indices:
        marker_rows = np.flatnonzero(matrix[i] > 0) if matrix.shape[0] else ()
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
    return total_trips, unassigned_trips


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


def _is_pairs_frame(frame: Any) -> bool:
    """Файл OD-пар (строковый или позиционный формат) вместо плотной матрицы."""
    columns = set(getattr(frame, "columns", ()))
    return (
        set(_PAIRS_ID_COLUMNS).issubset(columns)
        or set(_PAIRS_POS_COLUMNS).issubset(columns)
    )


def _read_matrix_frame(
    matrix_path: Path,
    ids_list: Sequence[str],
    reporter: Any,
) -> Any:
    """Читает матрицу из файла или восстанавливает из пар.

    Плотный формат выравнивается по ``ids_list`` (переиндексация). Если
    файла нет или он в формате пар, матрица собирается через
    ``_frame_from_pairs``; при этом выводится соответствующее сообщение.
    """
    import pandas as pd

    if matrix_path.exists():
        probe = _read_frame(matrix_path)
        if _is_pairs_frame(probe):
            # Файл пар: плотной матрицы нет — восстанавливаем из пар.
            return _frame_from_pairs(matrix_path, ids_list)
        probe.columns = [str(c) for c in probe.columns]
        if not isinstance(probe.index, pd.RangeIndex) or len(probe) != len(ids_list):
            probe.index = [str(v) for v in probe.index]
            probe = probe.reindex(index=ids_list, columns=ids_list)
        return probe
    line = getattr(reporter, "line", None)
    if line:
        line("  Плотная матрица не найдена: восстанавливаю из OD-пар")
    return _frame_from_pairs(matrix_path, ids_list)


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
    line = getattr(reporter, "line", None)
    if line:
        line(f"  Загрузка OD из файлов: {matrix_path}, {zones_path}")
    zones, weights, _attraction = load_zones_from_file(zones_path, reporter=None)
    ids = zones.ids
    ids_list = [str(int(v)) for v in ids]  # parquet может хранить id как строки
    matrix_path = Path(matrix_path)
    mdf = _read_matrix_frame(matrix_path, ids_list, reporter)
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
    """Собирает квадратную матрицу по зонам из OD-пар (без плотного parquet).

    Позиционный формат (``oi/di/trips``) собирается прямой индексацией;
    строковый (``orig_zone/dest_zone/trips``) — через сводную таблицу.
    """
    import pandas as pd

    df = _read_frame(pairs_path)
    n = len(ids)
    if set(_PAIRS_POS_COLUMNS).issubset(df.columns):
        try:
            oi = np.asarray(df[_PAIRS_POS_COLUMNS[0]].to_numpy(), dtype=np.int64)
            di = np.asarray(df[_PAIRS_POS_COLUMNS[1]].to_numpy(), dtype=np.int64)
            vals = np.asarray(df[_PAIRS_POS_COLUMNS[2]].to_numpy(), dtype=np.float64)
        except (TypeError, ValueError) as exc:
            raise OdMatrixError(f"{pairs_path}: битые колонки oi/di/trips") from exc
        ok = (oi >= 0) & (oi < n) & (di >= 0) & (di < n) & np.isfinite(vals)
        mat = np.zeros((n, n), dtype=np.float64)
        np.add.at(mat, (oi[ok], di[ok]), vals[ok])
        return pd.DataFrame(mat, index=list(ids), columns=list(ids))
    if not set(_PAIRS_ID_COLUMNS).issubset(df.columns):
        raise OdMatrixError(
            f"{pairs_path}: ожидаются колонки orig_zone/dest_zone/trips "
            "или oi/di/trips"
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
        if matrix.size > _OD_DENSE_FRAME_CELLS:
            _write_frame(
                _pairs_frame(matrix, ids, positions=True),
                tmp / "matrix_pairs.parquet",
            )
            matrix_format = "pairs"
        else:
            _write_frame(
                pd.DataFrame(matrix, index=ids, columns=ids),
                tmp / "matrix.parquet",
            )
            matrix_format = "dense"
        if not euclidean:
            # Евклидовы времена дёшево пересчитываются из зон при чтении —
            # плотный costs.npy (сотни МБ на больших сетках) не храним.
            np.save(tmp / "costs.npy", costs)
        meta: dict[str, Any] = {
            "road_ways": int(road_ways),
            "euclidean": bool(euclidean),
            "schema_version": _OD_CACHE_SCHEMA_VERSION,
            "matrix_format": matrix_format,
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


def _cache_any(cache_dir: Path, *names: str) -> Path | None:
    """Первый существующий файл из списка имён внутри ``cache_dir``."""
    for name in names:
        path = cache_dir / name
        if path.is_file():
            return path
    return None


def _load_cached_matrix(
    cache_dir: Path, ids_list: Sequence[str], meta: dict[str, Any]
) -> np.ndarray | None:
    """Матрица из кэша (плотная или пары); None при отсутствии файла."""
    if meta.get("matrix_format", "dense") == "pairs":
        pairs_path = _cache_any(cache_dir, "matrix_pairs.parquet", "matrix_pairs.csv")
        if pairs_path is None:
            return None
        return _frame_from_pairs(pairs_path, ids_list).to_numpy(dtype=np.float64)
    matrix_path = _cache_any(cache_dir, "matrix.parquet", "matrix.csv")
    if matrix_path is None:
        return None
    mdf = _read_frame(matrix_path)
    mdf.index = [str(v) for v in mdf.index]
    mdf.columns = [str(c) for c in mdf.columns]
    mdf = mdf.reindex(index=ids_list, columns=ids_list)
    return mdf.to_numpy(dtype=np.float64)


def _load_cached_costs(
    cache_dir: Path, meta: dict[str, Any], zones: Zones
) -> np.ndarray | None:
    """Времена из ``costs.npy`` или пересчёт по евклиду; None при промахе."""
    costs_path = _cache_any(cache_dir, "costs.npy")
    if costs_path is not None:
        return np.load(costs_path, allow_pickle=False)
    if not meta.get("euclidean"):
        return None
    # costs.npy не хранится для евклидова пути — пересчёт из зон.
    return euclidean_costs(zones)


def _load_purposes_from_meta(meta: dict[str, Any]) -> tuple[Any, ...]:
    """Разбирает секцию ``purposes`` из meta; при отсутствии — пустые кортежи."""
    purpose_meta = meta.get("purposes")
    if not (
        isinstance(purpose_meta, dict)
        and purpose_meta.get("out")
        and purpose_meta.get("ret")
    ):
        return (), (), ()
    periods = periods_from_purpose_blend(purpose_meta["out"], purpose_meta["ret"])
    trips = tuple(float(v) for v in purpose_meta.get("totals", ()))
    keys = tuple(str(v) for v in purpose_meta.get("keys", ()))
    return periods, trips, keys


def _load_districts_from_meta(meta: dict[str, Any], zones: Zones) -> tuple[str, ...]:
    """Названия районов из meta, если их число совпадает с числом зон."""
    names = tuple(str(v) for v in meta.get("districts", ()))
    return names if len(names) == len(zones) else ()


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

    zones_path = _cache_any(cache_dir, "zones.parquet", "zones.csv")
    meta_path = _cache_any(cache_dir, "meta.json")
    if not (zones_path and meta_path):
        return None
    try:
        zones, production, _attraction = load_zones_from_file(
            zones_path, reporter=None
        )
        ids_list = [str(int(v)) for v in zones.ids]
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        matrix = _load_cached_matrix(cache_dir, ids_list, meta)
        if matrix is None:
            return None
        costs = _load_cached_costs(cache_dir, meta, zones)
        if costs is None:
            return None
        if meta.get("schema_version") != _OD_CACHE_SCHEMA_VERSION:
            if line:
                line(
                    f"  Кэш OD устарел (версия {meta.get('schema_version')}), "
                    "пересчёт..."
                )
            return None
        purpose_periods, purpose_trips, purpose_keys = _load_purposes_from_meta(meta)
        district_names = _load_districts_from_meta(meta, zones)
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
                "Нет границы города и не задан od_zones_file: "
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


def _assign_districts_if_requested(
    config: Any, zones: Zones
) -> tuple[str, ...]:
    """Названия районов для зон, если задан ``od_districts_file``; иначе ``()``."""
    districts_file = getattr(config, "od_districts_file", None)
    if not districts_file:
        return ()
    return assign_district_names(zones, load_districts(districts_file))


def _apply_street_weights(
    zones: Zones,
    config: Any,
    out_dir: str | Path | None,
    city_slug: str,
    reporter: Any,
) -> np.ndarray | None:
    """Пересчитывает веса зон по рёбрам спроса; None, если файл не задан."""
    weights_file = getattr(config, "od_weights_file", None)
    if not weights_file:
        return None
    street_edges = load_demand_streets(weights_file)
    production = zone_weights_from_streets(zones, street_edges)
    if out_dir is not None:
        _write_demand_street_geojson(
            zones,
            street_edges,
            Path(out_dir) / f"{city_slug}_od_street_demand.geojson",
        )
    reporter.line(
        f"  Веса зон: по рёбрам спроса {Path(weights_file).name} "
        f"({len(street_edges):,} рёбер)"
    )
    return production


def _compute_costs(
    zones: Zones,
    geo_boundary: Any,
    session: Any,
    cache: JsonCache,
    city_slug: str,
    config: Any,
    reporter: Any,
) -> tuple[np.ndarray, bool, list | None]:
    """Считает времена между зонами.

    Возвращает ``(costs, euclidean, ways)``: при недоступной дорожной сети
    ``euclidean=True``, ``ways=None`` и времена по прямой.
    """
    ways = fetch_road_ways(
        geo_boundary, session, cache, cache_key=city_slug, config=config
    )
    if ways is None:
        reporter.line("  Дорожная сеть недоступна — время по прямой")
        return euclidean_costs(zones), True, None
    return zone_network_costs(zones, build_road_graph(ways)), False, ways


def _compute_purpose_matrix(
    production: np.ndarray,
    zones: Zones,
    config: Any,
    reporter: Any,
    *,
    attraction: np.ndarray | None = None,
) -> tuple[np.ndarray, tuple[Any, ...], list[float], list[str], dict[str, Any], Any]:
    """Строит OD по целям поездок и метаданные для кэша/отчёта.

    Возвращает ``(matrix, purpose_periods, purpose_trips, purpose_keys,
    purpose_meta, purpose_od)``.
    """
    purpose_od = build_purpose_od(
        production,
        zones,
        attraction=attraction,
        purposes=getattr(config, "od_purposes_definitions", PURPOSE_DEFAULTS),
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
    return matrix, purpose_periods, purpose_trips, purpose_keys, purpose_meta, purpose_od


def _try_cached_od(
    cache_dir: Path | None,
    cache: JsonCache,
    reporter: Any,
    *,
    city_slug: str,
    out_dir: str | Path | None,
    production: np.ndarray,
    attraction: np.ndarray,
    districts_file: str | None,
) -> OdResult | None:
    """Загружает и публикует OD из кэша; None, если кэш пуст/недоступен."""
    if cache_dir is None or not getattr(cache, "read_enabled", True):
        return None
    cached = _load_generated_od(cache_dir, reporter=reporter)
    if cached is None:
        return None
    save_od_outputs(
        cached.zones,
        cached.matrix,
        city=city_slug,
        out_dir=out_dir,
        production=production,
        attraction=attraction,
        purpose_trips=cached.purpose_trips or None,
        purpose_keys=cached.purpose_keys or None,
        district_names=(cached.district_names or None) if districts_file else None,
        write_matrix_frame=_dense_frame_wanted(cached.zones),
    )
    n_pairs = int((cached.matrix > 0).sum())
    reporter.line(
        f"  Зон: {len(cached.zones)}, поездок: "
        f"{float(cached.matrix.sum()):,.0f}, OD-пар: {n_pairs:,} (из кэша)"
    )
    return cached


def _export_takt_json(
    zones: Zones,
    matrix: np.ndarray,
    *,
    costs: np.ndarray,
    production: np.ndarray,
    attraction: np.ndarray,
    purpose_od: Any | None,
    city: str,
    out_dir: str | Path | None,
    passenger_flow: bool,
) -> list[Path]:
    """Экспортирует OD-результат в JSON-форматы пакета Takt.

    Выполняется только для полного пассажиропотока (``--passenger-flow``):
    ``{city}_od_demand.json`` — полный спрос (вся матрица), а при
    расчёте по целям ещё ``{city}_od_purposes.bin.json`` — слои целей
    (сумма слоёв равна полному спросу). Файлы автономны: при обратном
    чтении через ``od_demand_file``/``od_purposes_file`` передаётся один
    из них, иначе поездки посчитаются дважды.
    """
    if not passenger_flow or out_dir is None:
        return []
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    demand_path = out / f"{city}_od_demand.json"
    write_takt_demand(
        zones,
        matrix,
        production=production,
        attraction=attraction,
        costs=costs,
        path=demand_path,
    )
    written = [demand_path]
    if purpose_od is not None:
        purposes_path = out / f"{city}_od_purposes.bin.json"
        write_takt_purposes(purpose_od, costs, purposes_path)
        written.append(purposes_path)
    return written


def _run_external_matrix(config: Any, reporter: Any) -> OdResult:
    """Загружает готовую матрицу внешней модели (``od_matrix_file``)."""
    zones_file = getattr(config, "od_zones_file", None)
    if not zones_file:
        raise OdMatrixError(
            "Задана готовая матрица od_matrix_file, но не задан "
            "od_zones_file: зоны нужны для выравнивания матрицы"
        )
    return load_od_from_files(config.od_matrix_file, zones_file, reporter=reporter)


def _apply_takt_purposes(
    demand: Any,
    purposes_file: str | Path,
    reporter: Any,
) -> tuple[np.ndarray, tuple[Any, ...], list[float], list[str]]:
    """Разбивает спрос Takt по целям из файла ``od_purposes_file``."""
    purposes = load_takt_purposes(purposes_file)
    matrix, period_out, period_ret, keys, layer_trips = (
        build_takt_demand_with_purposes(demand, purposes)
    )
    purpose_periods = periods_from_purpose_blend(period_out, period_ret)
    purpose_trips = list(layer_trips)
    purpose_keys = list(keys)
    saved = ", ".join(f"{k}={v:,.0f}" for k, v in zip(purpose_keys, purpose_trips))
    reporter.line(
        f"  Цели Takt: {len(purposes.layers)} слоёв из "
        f"{Path(purposes_file).name}; полная матрица "
        f"{float(matrix.sum()):,.0f} поездок ({saved})"
    )
    return matrix, purpose_periods, purpose_trips, purpose_keys


def _run_takt_demand(
    config: Any,
    reporter: Any,
    *,
    city_slug: str,
    out_dir: str | Path | None,
) -> OdResult:
    """Строит OD из готового спроса Takt (``od_demand_file``)."""
    demand = load_takt_demand(config.od_demand_file)
    district_names = _assign_districts_if_requested(config, demand.zones)
    total_trips = float(demand.matrix.sum())
    n_pairs = int((demand.matrix > 0).sum())
    reporter.line(
        f"  Спрос Takt: зон {len(demand.zones):,}, поездок "
        f"{total_trips:,.0f}, OD-пар {n_pairs:,} "
        f"({Path(config.od_demand_file).name})"
    )
    purpose_periods: tuple[Any, ...] = ()
    purpose_trips: list[float] | None = None
    purpose_keys: list[str] | None = None
    matrix = demand.matrix
    purposes_file = getattr(config, "od_purposes_file", None)
    if purposes_file:
        matrix, purpose_periods, purpose_trips, purpose_keys = _apply_takt_purposes(
            demand, purposes_file, reporter
        )
    save_od_outputs(
        demand.zones,
        matrix,
        city=city_slug,
        out_dir=out_dir,
        production=demand.production,
        attraction=demand.production,
        purpose_trips=purpose_trips or None,
        purpose_keys=purpose_keys or None,
        district_names=district_names or None,
        write_matrix_frame=_dense_frame_wanted(demand.zones),
    )
    return OdResult(
        zones=demand.zones,
        matrix=matrix,
        costs=euclidean_costs(demand.zones),
        weights=demand.production,
        road_ways=0,
        euclidean=True,
        sparse_matrix=_as_sparse(matrix),
        purpose_periods=purpose_periods,
        purpose_trips=tuple(purpose_trips) if purpose_trips else (),
        purpose_keys=tuple(purpose_keys) if purpose_keys else (),
        district_names=district_names,
    )


def _run_generated_od(
    *,
    boundary: Any,
    config: Any,
    session: Any,
    cache: JsonCache,
    city_slug: str,
    reporter: Any,
    out_dir: str | Path | None,
) -> OdResult:
    """Строит OD на лету: зоны, веса, кэш, сеть, матрица, экспорт."""
    zones, production, attraction, weights, geo_boundary = _prepare_generation(
        boundary, config, reporter
    )
    override = _apply_street_weights(zones, config, out_dir, city_slug, reporter)
    if override is not None:
        production = override
        attraction = override.copy()
        weights = override
    districts_file = getattr(config, "od_districts_file", None)
    district_names = _assign_districts_if_requested(config, zones)
    cache_dir = _od_generated_cache_dir(
        cache,
        city_slug=city_slug,
        boundary=geo_boundary,
        config=config,
        zones_file=getattr(config, "od_zones_file", None),
    )
    cached = _try_cached_od(
        cache_dir,
        cache,
        reporter,
        city_slug=city_slug,
        out_dir=out_dir,
        production=production,
        attraction=attraction,
        districts_file=districts_file,
    )
    if cached is not None:
        return cached
    costs, euclidean, ways = _compute_costs(
        zones, geo_boundary, session, cache, city_slug, config, reporter
    )
    purpose_meta: dict[str, Any] | None = None
    purpose_periods: tuple[Any, ...] = ()
    purpose_trips: list[float] | None = None
    purpose_keys: list[str] | None = None
    purpose_od: Any | None = None
    if getattr(config, "od_purposes", False):
        (
            matrix,
            purpose_periods,
            purpose_trips,
            purpose_keys,
            purpose_meta,
            purpose_od,
        ) = _compute_purpose_matrix(
            production,
            zones,
            config,
            reporter,
            attraction=attraction,
        )
    else:
        # Единый OD-движок: Takt. Без разложения по целям
        # используется та же модель с профилями PURPOSE_DEFAULTS.
        purpose_od = build_purpose_od(production, zones)
        matrix = purpose_od.matrix
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
        write_matrix_frame=_dense_frame_wanted(zones),
    )
    takt_json = _export_takt_json(
        zones,
        matrix,
        costs=costs,
        production=production,
        attraction=attraction,
        purpose_od=purpose_od if getattr(config, "od_purposes", False) else None,
        city=city_slug,
        out_dir=out_dir,
        passenger_flow=bool(getattr(config, "passenger_flow", False)),
    )
    if takt_json:
        reporter.line("  JSON Takt: " + ", ".join(p.name for p in takt_json))
    total = max(float(matrix.sum()), 1.0)
    n_pairs = int((matrix > 0).sum())
    avg_time = (
        float((matrix * np.where(np.isfinite(costs), costs, 0.0)).sum() / total)
        if n_pairs > 1
        else 0.0
    )
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
    """Выполняет стадию OD-матрицы; None, если стадия отключена.

    Диспетчеризует по источнику спроса: готовая матрица внешней модели
    (``od_matrix_file``), готовый спрос Takt (``od_demand_file``) или
    расчёт на лету (границы зон/дороги/тяготение).
    """
    if not config.od_matrix:
        return None
    reporter.line("\n[OD] Матрица корреспонденций...")
    if getattr(config, "od_matrix_file", None):
        return _run_external_matrix(config, reporter)
    if getattr(config, "od_demand_file", None):
        return _run_takt_demand(
            config, reporter, city_slug=city_slug, out_dir=out_dir
        )
    return _run_generated_od(
        boundary=boundary,
        config=config,
        session=session,
        cache=cache,
        city_slug=city_slug,
        reporter=reporter,
        out_dir=out_dir,
    )