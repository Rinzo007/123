"""Население из растра GHS-POP (GHSL, прогноз 2030) как контрольный итог.

Зачем: оценка снизу вверх по зданиям систематически смещена. В паке Тамбова
она дала 542 330 человек против 272 460 в растре - ровно вдвое. Форма
распределения при этом своя и полезная, а величина должна прийти из
независимого источника. Так и делает автор киевского бандла: население
привязано к официальному итогу, а раскладывается по площади зданий.

Про сам растр. Описание канала в файле - `built_S_NRES_GHS_wOSM_100m_v1_2030`,
то есть по названию это нежилая застроенная площадь. Значениям это
противоречит: 79.12 чел./га в центре Воронежа, 1 064 820 по городу, и
поштучное совпадение с населением по зонам бандла Воронежа до единиц. Тег
канала - остаточный, данные population. Поэтому опираемся на величины, а не
на тег, и записываем это в provenance.

Растры в этом окружении покрывают лишь часть областей: у Воронежа 63.6%
заполненных ячеек, у Тамбова 59%, у Рязани 5.9%. Доля заполнения идёт в
provenance, потому что дырка в растре - это недооценка, а не нулевое
население.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from .geo import Point

# Ячейка GHS-POP 100 м в градусах: 0.000833333... = 100 м / (111320 м/°).
GHS_POP_CELL_DEG = 0.00083333333
# Число 2030 входит в имя файла: это прогноз, а не текущая перепись, и
# сравнивать его надо с прогнозом же.
GHS_POP_EPOCH = "2030"
GHS_POP_NODATA = -99999.0
GHS_POP_SUFFIX = "pop_"
# Порог, выше которого окно читается с усреднением по блокам.
MAX_POPULATION_CELLS = 4_000_000
# Каталоги, где ищется растр. Константа, а не литерал в функции: список должен
# подменяться в тестах, иначе выбор источника нельзя проверить.
SEARCH_DIRS = (r"D:\Programs\1234\GHS", "/data/ghs")


@dataclass(frozen=True, slots=True)
class PopulationRaster:
    """Растр населения: путь, откуда он и что о нём известно."""

    path: Path

    @property
    def name(self) -> str:
        return self.path.name

    def fingerprint(self) -> dict:
        """Идентичность растра для provenance: имя, размер, время, хеш префикса.

        Полный sha256 гигабайтного растра не считается - это минуты ради
        значения, которое и так стабильно. Хеш первых мегабайт плюс размер и
        время изменения достаточно, чтобы отличить другой файл того же имени.
        """
        stat = self.path.stat()
        with self.path.open("rb") as handle:
            head = handle.read(4 * 1024 * 1024)
        return {
            "file": self.name,
            "bytes": stat.st_size,
            "modified": int(stat.st_mtime),
            "headSha256": hashlib.sha256(head).hexdigest(),
            "epoch": GHS_POP_EPOCH,
            "kind": "population (raster tag says built_S_NRES; values are people/cell)",
        }


def find_population_raster(
    bbox: tuple[float, float, float, float] | None = None,
    explicit: str | Path | None = None,
) -> Path | None:
    """Ищет растр населения.

    При заданном bbox выбирается растр, который реально покрывает область, а
    не первый по алфавиту: в каталоге лежат десятки файлов разных стран, и
    `arm_pop` в алфавитном порядке опережает `rus_pop`, молча уводя читателя
    в Армению. Явный путь всегда побеждает.
    """
    candidates: list[Path] = []
    if explicit is not None:
        explicit_path = Path(explicit)
        if explicit_path.is_file():
            return explicit_path
    for directory in SEARCH_DIRS:
        root = Path(directory)
        if root.is_dir():
            candidates.extend(sorted(root.glob(f"*{GHS_POP_SUFFIX}*.tif")))

    if not candidates:
        return None
    if bbox is None:
        return candidates[0]
    return _best_coverage(candidates, bbox)


def _best_coverage(
    candidates: list[Path],
    bbox: tuple[float, float, float, float],
) -> Path | None:
    """Растр, покрывающий область, при равенстве - самый специфичный.

    Критерий именно покрытие запрошенной области, а не размер перекрытия:
    rus_pop геометрически простирается от -180 до +180 по долготе, поэтому
    перекрытие с Киевом у него ненулевое, и он побеждал ukr_pop, который
    покрывает область целиком. При равном покрытии берётся растр с меньшим
    extent - он и есть точным источником для этой территории.
    """
    try:
        import rasterio
    except ImportError:  # pragma: no cover - зависит от окружения
        return candidates[0]

    south, west, north, east = bbox
    wanted_area = max((east - west) * (north - south), 1e-12)
    best: tuple[float, float, Path] | None = None
    for candidate in candidates:
        try:
            with rasterio.open(candidate) as dataset:
                bounds = dataset.bounds
        except Exception:
            continue
        overlap_lon = min(bounds.right, east) - max(bounds.left, west)
        overlap_lat = min(bounds.top, north) - max(bounds.bottom, south)
        if overlap_lon <= 0 or overlap_lat <= 0:
            continue
        coverage = (overlap_lon * overlap_lat) / wanted_area
        extent = (bounds.right - bounds.left) * (bounds.top - bounds.bottom)
        # Сортировка: больше покрытие, потом меньше extent.
        key = (coverage, -extent)
        if best is None or key > (best[0], -best[1]):
            best = (coverage, extent, candidate)
    return best[2] if best else None


@dataclass(frozen=True, slots=True)
class PopulationCell:
    """Ячейка растра: где и сколько людей."""

    location: Point
    people: float


@dataclass(slots=True)
class PopulationRead:
    """Результат чтения растра вместе с честной статистикой покрытия."""

    cells: tuple[PopulationCell, ...] = ()
    total_people: float = 0.0
    cells_with_data: int = 0
    cells_in_window: int = 0
    max_people_per_cell: float = 0.0

    @property
    def coverage(self) -> float:
        if not self.cells_in_window:
            return 0.0
        return self.cells_with_data / self.cells_in_window

    def to_provenance(self) -> dict:
        return {
            "cellsWithData": self.cells_with_data,
            "cellsInWindow": self.cells_in_window,
            "coverage": round(self.coverage, 4),
            "totalPeople": round(self.total_people, 1),
            "maxPeoplePerCell": round(self.max_people_per_cell, 2),
            "cellAreaM2": round((GHS_POP_CELL_DEG * 111_320.0) ** 2, 1),
        }


def read_population_cells(
    bbox: tuple[float, float, float, float],
    raster: PopulationRaster,
) -> PopulationRead:
    """Читает население по ячейкам внутри bbox.

    Ячейки без данных не считаются нулём: они остаются пропуском, и доля
    пропуска уходит в provenance. Иначе дырка в растре выглядела бы как
    необитаемая земля.
    """
    try:
        import numpy as np
        import rasterio
        from rasterio.warp import transform as warp_transform
        from rasterio.windows import from_bounds
    except ImportError as exc:  # pragma: no cover - зависит от окружения
        raise RuntimeError(
            "Чтение растра населения требует rasterio: pip install 'transit-planner[gis]'"
        ) from exc

    south, west, north, east = bbox
    with rasterio.open(raster.path) as dataset:
        raster_crs = dataset.crs
        bounds = dataset.bounds
        # Пересечение с растром считается явно: окно целиком снаружи даёт
        # пустое чтение, а не ошибку доступа к пикселям.
        overlap_west = max(west, bounds.left)
        overlap_east = min(east, bounds.right)
        overlap_south = max(south, bounds.bottom)
        overlap_north = min(north, bounds.top)
        if overlap_east <= overlap_west or overlap_north <= overlap_south:
            return PopulationRead()

        window = from_bounds(
            overlap_west, overlap_south, overlap_east, overlap_north,
            transform=dataset.transform,
        )
        cells_wide = max(1, int(round(window.width)))
        cells_high = max(1, int(round(window.height)))
        step = 1
        if cells_wide * cells_high > MAX_POPULATION_CELLS:
            # Большое окно читается усреднением по блокам, сумма потом
            # домножается на число исходных ячеек: Resampling.sum при чтении
            # запрещён, а одно среднее потеряло бы население.
            step = int((cells_wide * cells_high / MAX_POPULATION_CELLS) ** 0.5) + 1
        out_shape = (max(1, cells_high // step), max(1, cells_wide // step))
        block = dataset.read(
            1,
            window=window,
            out_shape=out_shape,
            resampling=rasterio.enums.Resampling.average,
            boundless=True,
            fill_value=GHS_POP_NODATA,
        ) * (step * step)
        nodata = dataset.nodata
        transform = dataset.window_transform(window)
        if step > 1:
            transform = transform * rasterio.Affine.scale(step, step)
        cells_in_window = out_shape[0] * out_shape[1]

    values = np.where(block == nodata, np.nan, block.astype("float64"))
    values = np.where(np.isfinite(values) & (values < 0), np.nan, values)
    rows, cols = np.nonzero(~np.isnan(values))
    filled = int(rows.size)
    if filled == 0:
        return PopulationRead(cells_in_window=cells_in_window)

    centers_x = transform.c + (cols + 0.5) * transform.a
    centers_y = transform.f + (rows + 0.5) * transform.e
    if str(raster_crs).upper() not in {"EPSG:4326", ""}:
        centers_x, centers_y = warp_transform(
            raster_crs, "EPSG:4326", centers_x.tolist(), centers_y.tolist()
        )
    else:
        centers_x = centers_x.tolist()
        centers_y = centers_y.tolist()

    cells = tuple(
        PopulationCell(location=Point(lon, lat), people=float(people))
        for lon, lat, people in zip(centers_x, centers_y, values[rows, cols].tolist())
        if people > 0
    )
    return PopulationRead(
        cells=cells,
        total_people=sum(cell.people for cell in cells),
        cells_with_data=filled,
        cells_in_window=cells_in_window,
        max_people_per_cell=max((cell.people for cell in cells), default=0.0),
    )


def population_by_zone(
    read: PopulationRead,
    zones: tuple,
    *,
    origin_lon: float,
    origin_lat: float,
) -> dict[str, float]:
    """Разносит население растра по зонам по ближайшему центроиду.

    Ячейка 100 м мельче ячеек TAZ, поэтому «ближайший центроид» не искажает
    распределение, но не вычитает дырки: соседняя зона получает столько же,
    сколько и её сосед по ячейке.
    """
    from .projection import project_wgs84_point

    if not read.cells or not zones:
        return {}
    totals: dict[str, float] = {zone.id: 0.0 for zone in zones}
    for cell in read.cells:
        point = project_wgs84_point(
            cell.location, origin_lon=origin_lon, origin_lat=origin_lat
        )
        nearest = min(
            zones,
            key=lambda zone: (
                (point.x - zone.centroid_x) ** 2 + (point.y - zone.centroid_y) ** 2
            ),
            default=None,
        )
        if nearest is None:
            continue
        totals[nearest.id] += cell.people
    return {zone_id: people for zone_id, people in totals.items() if people > 0}
