"""Область исследования по границе города.

Границы в `D:\\Programs\\1234` - это не административные границы, а контуры
застроенной части, выведенные из зданий Overture. Для транспортной модели это
правильнее административной границы: моделировать надо то, где есть город, а
не где кончается регион.

Честное ограничение: провайдеры Overture умеют фильтровать только по bbox, а не
по полигону. Поэтому фактически отбор идёт по прямоугольнику, описанному
границей, и часть зданий вне полигона тоже попадёт в пак. Площадь этой
разницы считается здесь и записывается в provenance - иначе выглядело бы,
будто отбор точный.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path

# Метры в градусе на широте: 1° широты ~110.574 км, 1° долготы ~111.320·cos(lat).
_M_PER_DEG_LAT = 110_574.0
_M_PER_DEG_LON = 111_320.0


@dataclass(frozen=True, slots=True)
class StudyArea:
    """Область исследования: bbox для отбора плюс свойства границы."""

    name: str
    bbox: tuple[float, float, float, float]
    polygon_area_m2: float
    ring_count: int
    vertex_count: int
    source: str
    file: str
    sha256: str
    polygons: tuple = ()

    def contains(self, lon: float, lat: float) -> bool:
        """Точка внутри границы. Пустая геометрия пропускает всё."""
        if not self.polygons:
            return True
        return point_in_rings(lon, lat, list(self.polygons))

    def contains_point(self, point) -> bool:
        """Точка внутри границы, если точка задана объектом Point."""
        return self.contains(point.x, point.y)

    @property
    def bbox_area_m2(self) -> float:
        south, west, north, east = self.bbox
        width = (east - west) * _M_PER_DEG_LON * math.cos(math.radians((south + north) / 2))
        height = (north - south) * _M_PER_DEG_LAT
        return width * height

    @property
    def extra_area_fraction(self) -> float:
        """Доля bbox, не занятая полигоном: столько лишнего попадёт в отбор."""
        bbox_area = self.bbox_area_m2
        if bbox_area <= 0:
            return 0.0
        return max(0.0, 1.0 - self.polygon_area_m2 / bbox_area)

    def to_provenance(self) -> dict:
        return {
            "name": self.name,
            "file": self.file,
            "sha256": self.sha256,
            "source": self.source,
            "bbox": list(self.bbox),
            "polygonAreaKm2": round(self.polygon_area_m2 / 1e6, 2),
            "bboxAreaKm2": round(self.bbox_area_m2 / 1e6, 2),
            "extraAreaFraction": round(self.extra_area_fraction, 3),
            "rings": self.ring_count,
            "vertices": self.vertex_count,
            "selection": "by bbox; off-polygon features are included",
        }


def _ring_area_m2(ring: list[list[float]], reference_lat: float) -> float:
    """Площадь кольца в м² через shoelace и равновеликую аппроксимацию.

    Кольцо в GeoJSON замкнуто по спецификации, поэтому последняя вершина
    повторяет первую. Если закруглить кольцо ещё раз, площадь удвоится - это
    и случилось на первой версии.
    """
    if len(ring) < 4:
        return 0.0
    vertices = [list(point) for point in ring]
    if vertices[0] != vertices[-1]:
        vertices.append(vertices[0])
    total = 0.0
    for (x0, y0), (x1, y1) in zip(vertices, vertices[1:]):
        total += x0 * y1 - x1 * y0
    degrees = abs(total) / 2.0
    return degrees * _M_PER_DEG_LAT * _M_PER_DEG_LON * math.cos(math.radians(reference_lat))


def _ring_bbox(ring: list[list[float]]) -> tuple[float, float]:
    lons = [point[0] for point in ring]
    lats = [point[1] for point in ring]
    return min(lats), max(lats)


def point_in_rings(
    lon: float,
    lat: float,
    polygons: list[list[list[list[float]]]],
) -> bool:
    """Внутри ли точка границы: внешнее кольцо минус дырки.

    Правило чётности: пересечения считаются по всем кольцам полигона, и
    нечётное число означает "внутри". Так автоматически вычитаются дырки
    (аэродром, промзона внутри контура). По нескольким полигонам - объединение.
    """
    for polygon in polygons:
        crossings = 0
        for ring in polygon:
            if len(ring) < 4:
                continue
            for (x0, y0), (x1, y1) in zip(ring, ring[1:]):
                if (y0 > lat) != (y1 > lat):
                    crossing = x0 + (lat - y0) * (x1 - x0) / (y1 - y0)
                    if crossing > lon:
                        crossings += 1
        if crossings % 2 == 1:
            return True
    return False


def load_boundary_geojson(
    path: str | Path,
    *,
    name: str | None = None,
) -> StudyArea:
    """Читает границу города и превращает её в область исследования.

    Поддерживаются Polygon и MultiPolygon. Пустая или вырожденная геометрия
    отвергается: молчаливый пустой bbox привёл бы к пак�� без данных.
    """
    source_path = Path(path)
    raw = source_path.read_bytes()
    data = json.loads(raw.decode("utf-8"))
    features = data.get("features") or []
    if not features:
        raise ValueError(f"boundary has no features: {source_path}")

    lons: list[float] = []
    lats: list[float] = []
    area_m2 = 0.0
    rings = 0
    vertices = 0
    sources: set[str] = set()
    polygons: list[list[list[list[float]]]] = []
    for feature in features:
        geometry = feature.get("geometry") or {}
        kind = geometry.get("type")
        if kind == "Polygon":
            feature_polygons = [geometry.get("coordinates") or []]
        elif kind == "MultiPolygon":
            feature_polygons = list(geometry.get("coordinates") or [])
        else:
            raise ValueError(f"unsupported boundary geometry: {kind!r}")
        source_label = (feature.get("properties") or {}).get("source")
        if source_label:
            sources.add(str(source_label))
        for polygon in feature_polygons:
            polygons.append(polygon)
            for ring in polygon:
                if len(ring) < 4:
                    continue
                rings += 1
                vertices += len(ring)
                lons.extend(point[0] for point in ring)
                lats.extend(point[1] for point in ring)
            # Площадь полигона: первое кольцо - внешнее, остальные - дырки и
            # вычитаются. Сложение дало бы завышенную площадь с вырезами.
            # Опорная широта от bbox кольца, а не от среднего по вершинам:
            # замкнутое кольцо дублирует вершину и сдвинуло бы среднее.
            for index, ring in enumerate(polygon):
                if len(ring) < 4:
                    continue
                ring_south, ring_north = _ring_bbox(ring)
                ring_area = _ring_area_m2(
                    [list(point) for point in ring], (ring_south + ring_north) / 2
                )
                area_m2 += ring_area if index == 0 else -ring_area

    if not lons or area_m2 <= 0:
        raise ValueError(f"boundary has no usable area: {source_path}")

    return StudyArea(
        name=name or source_path.stem.replace("_routes_boundary", ""),
        bbox=(min(lats), min(lons), max(lats), max(lons)),
        polygon_area_m2=area_m2,
        ring_count=rings,
        vertex_count=vertices,
        source="+".join(sorted(sources)) if sources else "unknown",
        file=source_path.name,
        sha256=hashlib.sha256(raw).hexdigest(),
        polygons=tuple(tuple(tuple(tuple(p) for p in ring) for ring in poly)
                       for poly in polygons),
    )
