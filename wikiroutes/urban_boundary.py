"""Граница города из растра GHS-BUILT-S (городская территория).

Алгоритм повторяет ``city.py``: вокруг центра города строится окно радиусом
``RADIUS_M``, застройка агрегируется до ячеек 500 м, выделяется «городская
территория» по критериям US Census Bureau (ядро >= 10 % застройки,
«раствор» >= 5 %, мин. площадь 25 км²), и возвращается полигон, содержащий
центр. Используется как запасной источник границы, когда города нет в UCDB
и OSM-граница непригодна.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from .errors import MissingDependencyError

logger = logging.getLogger("wikiroutes.urban")

RADIUS_M = 60000.0
CORE_THRESHOLD = 0.10
MORTAR_THRESHOLD = 0.05
MIN_AREA_KM2 = 25.0
MOLL_CRS = "ESRI:54009"
# По умолчанию растр GHS-BUILT-S (площадь застройки) в проекции Мольвейде.
DEFAULT_BUILT_S_FILE = (
    r"D:\Programs\Cities2\GHS\GHS_BUILT_S_E2030_GLOBE_R2023A_54009_100_V1_0.tif"
)


def _import_gis():
    """Лениво импортирует rasterio, pyproj, shapely; бросает MissingDependencyError."""
    try:
        import rasterio
        from pyproj import CRS, Transformer
        from shapely.geometry import Point
        from shapely.geometry import shape as shapely_shape
        from shapely.ops import transform as shapely_transform
        from shapely.ops import unary_union as shapely_unary_union
    except ImportError as exc:  # pragma: no cover
        raise MissingDependencyError(
            "Для построения границы из GHS-BUILT-S нужны: "
            "pip install rasterio pyproj shapely"
        ) from exc
    return {
        "rasterio": rasterio,
        "CRS": CRS,
        "Transformer": Transformer,
        "Point": Point,
        "shape": shapely_shape,
        "transform": shapely_transform,
        "unary_union": shapely_unary_union,
    }


def build_urban_boundary(
    lon: float,
    lat: float,
    tif_path: str | None = None,
    *,
    radius_m: float = RADIUS_M,
    core_threshold: float = CORE_THRESHOLD,
    mortar_threshold: float = MORTAR_THRESHOLD,
    min_area_km2: float = MIN_AREA_KM2,
) -> Any | None:
    """Возвращает полигон городской территории в WGS84 или None.

    ``lon/lat`` — центр города (обычно центроид OSM-границы), ``tif_path`` —
    растр GHS-BUILT-S (по умолчанию ``DEFAULT_BUILT_S_FILE``). Полигон —
    внешний контур застройки вокруг центра в координатах (lon, lat).
    """
    gis = _import_gis()
    rasterio = gis["rasterio"]
    CRS = gis["CRS"]
    Transformer = gis["Transformer"]
    Point = gis["Point"]
    shapely_shape = gis["shape"]

    tif = tif_path or DEFAULT_BUILT_S_FILE
    try:
        from rasterio.features import shapes as rio_shapes
        from rasterio.transform import Affine
        from rasterio.windows import from_bounds
        from scipy import ndimage
    except ImportError as exc:  # pragma: no cover
        raise MissingDependencyError("Требуется scipy/rasterio") from exc

    moll = CRS.from_user_input(MOLL_CRS)
    tr_4326_to_moll = Transformer.from_crs(
        CRS.from_epsg(4326), moll, always_xy=True
    )
    cx, cy = tr_4326_to_moll.transform(lon, lat)

    aeqd = f"+proj=aeqd +lat_0={lat} +lon_0={lon} +datum=WGS84 +units=m"
    try:
        import geopandas as gpd
    except ImportError as exc:  # pragma: no cover
        raise MissingDependencyError("Требуется geopandas") from exc

    circle = (
        gpd.GeoDataFrame(geometry=[Point(0, 0)], crs=aeqd)
        .buffer(radius_m)
        .to_crs(moll)
        .geometry.iloc[0]
    )

    try:
        with rasterio.open(tif) as src:
            win = from_bounds(*circle.bounds, src.transform)
            bu = src.read(1, window=win).astype("float64")
            nodata = src.nodata if src.nodata else 65535
            bu[bu >= nodata] = 0.0
            t = src.window_transform(win)
    except Exception as exc:  # noqa: BLE001 — повреждённый/отсутствующий растр
        logger.warning("Не удалось прочитать растр GHS-BUILT-S %s: %s", tif, exc)
        return None

    f = 5
    h, w = (bu.shape[0] // f) * f, (bu.shape[1] // f) * f
    bu_500 = bu[:h, :w].reshape(h // f, f, w // f, f).sum(axis=(1, 3))
    bu_frac = bu_500 / ((f * 100) ** 2)
    t_500 = t * Affine.scale(f)

    bricks = bu_frac >= core_threshold
    mortar_mask = bu_frac >= mortar_threshold
    urban = ndimage.binary_propagation(bricks, mask=mortar_mask)
    urban = ndimage.binary_closing(urban, structure=np.ones((3, 3)))
    urban = ndimage.binary_fill_holes(urban)

    lbl, n = ndimage.label(urban)
    sizes_km2 = ndimage.sum(urban, lbl, index=range(1, n + 1)) * 0.25
    urban = np.isin(lbl, np.nonzero(sizes_km2 >= min_area_km2)[0] + 1)

    geoms = [
        shapely_shape(g)
        for g, v in rio_shapes(urban.astype("uint8"), transform=t_500)
        if v == 1
    ]
    if not geoms:
        return None

    center_pt = Point(cx, cy)
    boundary = _pick_boundary_component(geoms, center_pt)
    if boundary is None:
        return None
    boundary = boundary.intersection(circle).simplify(200).buffer(0)
    if boundary.is_empty:
        return None

    boundary = gis["transform"](
        Transformer.from_crs(moll, "EPSG:4326", always_xy=True).transform,
        boundary,
    )
    return boundary if not boundary.is_empty else None


def _pick_boundary_component(
    geoms: Any, center_pt: Any, gap_m: float = 5000.0
) -> Any | None:
    """Выбирает «городскую территорию» для центра города.

    Ядро — компонента, содержащая центральную точку; если такой нет —
    ближайшая к центру (центроид границы города может лежать на реке,
    делящей город на берега). Затем к ядру присоединяются все компоненты,
    чьё минимальное расстояние до уже собранного набора не превышает
    ``gap_m`` (зазор между берегами одной реки), пока набор растёт. Так
    сохраняются оба берега (напр. Ульяновск) и не притягивается соседний
    город (напр. Нижний Новгород для Дзержинска, ~10+ км). Возвращает
    объединение кластера или ``None`` при пустом входе.
    """
    if not geoms:
        return None
    containing = [g for g in geoms if g.contains(center_pt)]
    if containing:
        seed = containing[0]
    else:
        seed = min(geoms, key=lambda g: g.distance(center_pt))

    cluster = [seed]
    remaining = [g for g in geoms if g is not seed]
    while True:
        joined = False
        for g in remaining:
            if min(g.distance(c) for c in cluster) <= gap_m:
                cluster.append(g)
                remaining.remove(g)
                joined = True
        if not joined:
            break

    if len(cluster) == 1:
        return cluster[0]
    return _import_gis()["unary_union"](cluster)


__all__ = [
    "DEFAULT_BUILT_S_FILE",
    "build_urban_boundary",
]