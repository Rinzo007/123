from __future__ import annotations

from dataclasses import dataclass
from math import asin, cos, floor, hypot, pi, radians, sin, sqrt
from typing import Iterable

from .geo import Point


@dataclass(frozen=True, slots=True)
class BuildingFootprint:
    id: str
    polygons: tuple[tuple[Point, ...], ...]
    area_m2: float
    subtype: str | None = None
    building_class: str | None = None
    is_underground: bool = False
    num_floors: float | None = None
    height_m: float | None = None


# Классы зданий, в которых живут люди. Промышленные и инфраструктурные в
# жилой фонд не входят, иначе грузовые терминалы считывались бы как жильё.
RESIDENTIAL_BUILDING_CLASSES = frozenset({
    "house",
    "residential",
    "apartments",
    "dormitory",
    "bungalow",
    "hut",
})

# Этажность, которой заменяют отсутствующее num_floors. Средняя по жилому
# фонду: низкие дома при одноэтажной застройке, высотки упираются в потолок.
_FALLBACK_FLOORS_MIN = 1.0
_FALLBACK_FLOORS_MAX = 8.0
# Номинальная высота жилого этажа. В Overture height включает конструктив и
# кровлю, поэтому из неё берётся грубая оценка с потолком, а не точное число.
_METERS_PER_FLOOR = 3.0
# Жилая площадь на одного человека. 30-40 м² — обычная величина для
# городской квартиры вместе с долей общих площадей.
FLOOR_AREA_PER_PERSON_M2 = 32.0


def is_residential_building(building: BuildingFootprint) -> bool:
    if building.is_underground:
        return False
    if building.building_class is not None:
        if building.building_class in RESIDENTIAL_BUILDING_CLASSES:
            return True
        return False
    # Без класса решение принимает подтип: Overture не заполняет class у части
    # зданий, но подтип остаётся.
    subtype = (building.subtype or "").lower()
    return "residential" in subtype or "house" in subtype or "apart" in subtype


def effective_floors(building: BuildingFootprint) -> float:
    """Число жилых этажей: из данных, иначе из высоты, иначе минимум."""
    if building.num_floors is not None:
        return min(max(building.num_floors, _FALLBACK_FLOORS_MIN), _FALLBACK_FLOORS_MAX)
    if building.height_m is not None:
        return min(
            max(building.height_m / _METERS_PER_FLOOR, _FALLBACK_FLOORS_MIN),
            _FALLBACK_FLOORS_MAX,
        )
    return _FALLBACK_FLOORS_MIN


def residential_floor_area_m2(building: BuildingFootprint) -> float:
    if not is_residential_building(building):
        return 0.0
    return building.area_m2 * effective_floors(building)


def population_from_buildings(
    buildings: Iterable[BuildingFootprint],
    *,
    floor_area_per_person_m2: float = FLOOR_AREA_PER_PERSON_M2,
) -> float:
    """Оценка населения по жилой площади этажей.

    Опора self-contained: перепись не нужна, всё считается из площади и
    этажности, которые Overture отдаёт по зданиям. Метод тот же, что у GHSL:
    объём жилого фонда делится на площадь на человека.
    """
    if floor_area_per_person_m2 <= 0:
        raise ValueError("floor_area_per_person_m2 must be positive")
    total = 0.0
    for building in buildings:
        total += residential_floor_area_m2(building)
    return total / floor_area_per_person_m2


@dataclass(frozen=True, slots=True)
class WaterFeature:
    id: str
    polygons: tuple[tuple[Point, ...], ...]
    water_class: str | None = None
    subtype: str | None = None


def parse_polygon_geometry(geometry: dict) -> tuple[tuple[Point, ...], ...]:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates") or []
    if geometry_type == "Polygon":
        return _polygon_rings(coordinates)
    if geometry_type == "MultiPolygon":
        polygons: list[tuple[Point, ...]] = []
        for polygon in coordinates:
            polygons.extend(_polygon_rings(polygon))
        return tuple(polygons)
    return ()


def polygon_area_m2(polygon: tuple[Point, ...]) -> float:
    if len(polygon) < 3:
        return 0.0
    lat0 = radians(sum(point.y for point in polygon) / len(polygon))
    scale_x = 6_378_137.0 * cos(lat0) * pi / 180.0
    scale_y = 6_378_137.0 * pi / 180.0
    area = 0.0
    for left, right in zip(polygon, polygon[1:] + polygon[:1]):
        area += left.x * right.y - right.x * left.y
    # Convert the lon/lat shoelace result to square metres.
    return abs(area) * 0.5 * scale_x * scale_y


class UrbanContext:
    """Coarse spatial context derived from Overture buildings and water."""

    CELL_DEG = 0.002

    def __init__(
        self,
        buildings: Iterable[BuildingFootprint] = (),
        water: Iterable[WaterFeature] = (),
    ) -> None:
        self.buildings = tuple(buildings)
        self.water = tuple(water)
        self._building_index = self._build_index(
            ((feature.id, feature.polygons) for feature in self.buildings)
        )
        self._water_index = self._build_index(
            ((feature.id, feature.polygons) for feature in self.water)
        )
        self._buildings_by_id = {feature.id: feature for feature in self.buildings}
        self._water_by_id = {feature.id: feature for feature in self.water}

    def segment_metrics(
        self,
        points: tuple[Point, ...],
    ) -> dict[str, float]:
        if len(points) < 2:
            return {
                "built_up": 0.0,
                "water_share": 0.0,
                "roof_share": 0.0,
                "building_count": 0.0,
                "building_area_m2": 0.0,
            }

        built_up = self._sample_polygon_share(points, self._building_index, self._buildings_by_id)
        water_share = self._sample_polygon_share(points, self._water_index, self._water_by_id)

        building_ids = self._intersecting_building_ids(points)
        return {
            "built_up": built_up,
            "water_share": water_share,
            "roof_share": built_up,
            "building_count": float(len(building_ids)),
            "building_area_m2": sum(
                self._buildings_by_id[item].area_m2 for item in building_ids
            ),
        }


    def _intersecting_building_ids(self, points: tuple[Point, ...]) -> set[str]:
        result: set[str] = set()
        for left, right in zip(points, points[1:]):
            min_x = min(left.x, right.x)
            max_x = max(left.x, right.x)
            min_y = min(left.y, right.y)
            max_y = max(left.y, right.y)
            for cell in self._cells_for_bbox(min_x, min_y, max_x, max_y):
                for feature_id in self._building_index.get(cell, ()):
                    if feature_id in result:
                        continue
                    feature = self._buildings_by_id[feature_id]
                    if any(_segment_intersects_polygon(left, right, polygon) for polygon in feature.polygons):
                        result.add(feature_id)
        return result

    def _sample_polygon_share(
        self,
        points: tuple[Point, ...],
        index: dict[tuple[int, int], set[str]],
        features: dict[str, object],
    ) -> float:
        total = 0
        inside = 0
        for left, right in zip(points, points[1:]):
            distance = _haversine_m(left, right)
            samples = max(2, min(200, round(distance / 60.0)))
            for step in range(samples + 1):
                fraction = step / samples
                sample = Point(
                    left.x + (right.x - left.x) * fraction,
                    left.y + (right.y - left.y) * fraction,
                )
                total += 1
                if self._point_in_any_polygon(sample, index, features):
                    inside += 1
        return inside / total if total else 0.0

    def _point_in_any_polygon(
        self,
        point: Point,
        index: dict[tuple[int, int], set[str]],
        features: dict[str, object],
    ) -> bool:
        cell = self._cell(point.x, point.y)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for feature_id in index.get((cell[0] + dx, cell[1] + dy), ()):
                    feature = features[feature_id]
                    if any(_point_in_polygon(point, polygon) for polygon in feature.polygons):
                        return True
        return False

    @classmethod
    def _build_index(
        cls,
        items: Iterable[tuple[str, tuple[tuple[Point, ...], ...]]],
    ) -> dict[tuple[int, int], set[str]]:
        index: dict[tuple[int, int], set[str]] = {}
        for feature_id, polygons in items:
            for polygon in polygons:
                if not polygon:
                    continue
                min_x = min(point.x for point in polygon)
                max_x = max(point.x for point in polygon)
                min_y = min(point.y for point in polygon)
                max_y = max(point.y for point in polygon)
                for cell in cls._cells_for_bbox_static(min_x, min_y, max_x, max_y):
                    index.setdefault(cell, set()).add(feature_id)
        return index

    @classmethod
    def _cells_for_bbox_static(
        cls,
        min_x: float,
        min_y: float,
        max_x: float,
        max_y: float,
    ) -> tuple[tuple[int, int], ...]:
        start_x = floor(min_x / cls.CELL_DEG)
        end_x = floor(max_x / cls.CELL_DEG)
        start_y = floor(min_y / (cls.CELL_DEG * 0.62))
        end_y = floor(max_y / (cls.CELL_DEG * 0.62))
        return tuple(
            (x, y)
            for x in range(start_x, end_x + 1)
            for y in range(start_y, end_y + 1)
        )

    def _cells_for_bbox(
        self,
        min_x: float,
        min_y: float,
        max_x: float,
        max_y: float,
    ) -> tuple[tuple[int, int], ...]:
        return self._cells_for_bbox_static(min_x, min_y, max_x, max_y)

    @classmethod
    def _cell(cls, x: float, y: float) -> tuple[int, int]:
        return floor(x / cls.CELL_DEG), floor(y / (cls.CELL_DEG * 0.62))



def _polygon_rings(coordinates) -> tuple[tuple[Point, ...], ...]:
    rings: list[tuple[Point, ...]] = []
    for ring in coordinates:
        points = tuple(
            Point(float(x), float(y))
            for x, y, *_ in ring
            if len((x, y)) >= 2
        )
        if len(points) >= 3:
            rings.append(points)
    return tuple(rings)


def _haversine_m(a: Point, b: Point) -> float:
    radius = 6_371_000.0
    lat1 = radians(a.y)
    lat2 = radians(b.y)
    dlat = radians(b.y - a.y)
    dlon = radians(b.x - a.x)
    h = sin(dlat / 2.0) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2.0) ** 2
    return 2.0 * radius * asin(min(1.0, sqrt(max(0.0, h))))


def _point_in_polygon(point: Point, polygon: tuple[Point, ...]) -> bool:
    inside = False
    for left, right in zip(polygon, polygon[1:] + polygon[:1]):
        if ((left.y > point.y) != (right.y > point.y)):
            denominator = right.y - left.y
            if abs(denominator) < 1e-15:
                continue
            x_intersection = (
                (right.x - left.x) * (point.y - left.y) / denominator
                + left.x
            )
            if point.x < x_intersection:
                inside = not inside
    return inside


def _segment_intersects_polygon(
    left: Point,
    right: Point,
    polygon: tuple[Point, ...],
) -> bool:
    if _point_in_polygon(left, polygon) or _point_in_polygon(right, polygon):
        return True
    for first, second in zip(polygon, polygon[1:] + polygon[:1]):
        if _segments_intersect(left, right, first, second):
            return True
    return False


def _segments_intersect(a: Point, b: Point, c: Point, d: Point) -> bool:
    def orientation(first: Point, second: Point, third: Point) -> float:
        return (
            (second.x - first.x) * (third.y - first.y)
            - (second.y - first.y) * (third.x - first.x)
        )

    o1 = orientation(a, b, c)
    o2 = orientation(a, b, d)
    o3 = orientation(c, d, a)
    o4 = orientation(c, d, b)

    if o1 == 0 and _on_segment(a, c, b):
        return True
    if o2 == 0 and _on_segment(a, d, b):
        return True
    if o3 == 0 and _on_segment(c, a, d):
        return True
    if o4 == 0 and _on_segment(c, b, d):
        return True
    return (o1 > 0) != (o2 > 0) and (o3 > 0) != (o4 > 0)


def _on_segment(a: Point, b: Point, c: Point) -> bool:
    return (
        min(a.x, c.x) - 1e-12 <= b.x <= max(a.x, c.x) + 1e-12
        and min(a.y, c.y) - 1e-12 <= b.y <= max(a.y, c.y) + 1e-12
    )
