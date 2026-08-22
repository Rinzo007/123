"""Направление маршрута как самостоятельная единица расчётов."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from .geometry import haversine_km
from .type_defs import DirectionKey

if TYPE_CHECKING:
    from .enums import RouteType
    from .models import Direction, RouteData, Stop
    from .type_defs import Coordinate

__all__ = [
    "DirUnit",
    "DirectionKey",
    "build_units",
    "dir_geo_sig",
    "network_center_distances",
]


class DirUnit:
    """Единица расчёта: одно направление маршрута.

    Лёгкий адаптер над ``(route, direction)``: делегирует координаты,
    остановки и длину направлению, идентификаторы — маршруту.

    ``__slots__`` экономит память при десятках тысяч единиц.
    """

    __slots__ = ("d", "di", "route", "unit_id")

    def __init__(
        self,
        route: RouteData,
        di: int,
        d: Direction,
        unit_id: int,
    ) -> None:
        self.route = route
        self.di = di
        self.d = d
        self.unit_id = unit_id

    @property
    def coords(self) -> tuple[Coordinate, ...]:
        return self.d.coords

    @property
    def stops(self) -> tuple[Stop, ...]:
        return self.d.stops

    @property
    def km(self) -> float:
        return self.d.km

    @property
    def route_id(self) -> int:
        return self.route.route_id

    @property
    def route_type(self) -> RouteType:
        return self.route.route_type

    @property
    def key(self) -> DirectionKey:
        """Канонический стабильный ключ направления.

        В отличие от позиционного ``unit_id``, ключ не зависит от порядка
        маршрутов во входном списке и пригоден для внешних результатов,
        кэшей и связывания статистик направления.
        """
        return (self.route_id, self.di)

    @property
    def stable_key(self) -> str:
        """Строковое представление стабильного ключа для отчётов/CLI."""
        route_id, direction_index = self.key
        return f"{route_id}:{direction_index}"

    @property
    def name(self) -> str:
        """Человекочитаемое имя направления.

        Если у направления есть собственное имя, используется оно.
        Иначе генерируется имя относительно маршрута и индекса направления.
        """
        if self.d.name:
            return self.d.name

        if self.di == 0:
            return f"{self.route.name} (туда)"

        if self.di == 1:
            return f"{self.route.name} (обратно)"

        return f"{self.route.name} (направление {self.di + 1})"


def build_units(routes: Iterable[RouteData]) -> list[DirUnit]:
    """Разворачивает маршруты в список направлений-единиц расчёта.

    Пропускает маршруты с ошибками/без направлений и направления
    с числом координат < 2.

    ``unit_id`` — локальный позиционный индекс для массивов/графов.
    Для любой долговечной идентификации следует использовать ``DirUnit.key``.
    """
    units: list[DirUnit] = []

    for route in routes:
        if route.error or not route.directions:
            continue

        for di, d in enumerate(route.directions):
            if len(d.coords) >= 2:
                units.append(DirUnit(route, di, d, len(units)))

    return units


def _rounded_coordinate(value: Any) -> float | None:
    """Округляет координату до 4 знаков и нормализует -0.0."""
    try:
        result = float(value)
    except TypeError, ValueError:
        return None

    if not math.isfinite(result):
        return None

    result = round(result, 4)

    if result == 0.0:
        return 0.0

    return result


def network_center_distances(units: list[DirUnit]) -> dict[DirectionKey, float]:
    """Расстояние (км) каждого направления до центра маршрутной сети.

    Центр сети — средняя точка (центроид центроидов) всех направлений.
    Расстояние направления — по гаверсинусам от его собственного
    центроида (средней координаты) до центра сети.

    Возвращает ``{DirectionKey: км}``; направления без координат пропускаются.
    """
    centroids: dict[DirectionKey, tuple[float, float]] = {}
    lats: list[float] = []
    lons: list[float] = []

    for unit in units:
        coords = getattr(unit, "coords", ())
        if not coords:
            continue

        try:
            lat_sum = sum(float(lat) for lat, _ in coords)
            lon_sum = sum(float(lon) for _, lon in coords)
        except TypeError, ValueError:
            continue

        centroid = (lat_sum / len(coords), lon_sum / len(coords))
        centroids[unit.key] = centroid
        lats.append(centroid[0])
        lons.append(centroid[1])

    if not lats:
        return {}

    center_lat = sum(lats) / len(lats)
    center_lon = sum(lons) / len(lons)

    return {
        direction_key: haversine_km(center_lat, center_lon, lat, lon)
        for direction_key, (lat, lon) in centroids.items()
    }


def dir_geo_sig(d: Direction) -> str:
    """Сигнатура геометрии направления (10 hex-символов).

    Координаты округляются до 4 знаков (~11 м) — аналогично
    ``common.route_geo_sig``.

    Дополнительно:
      - ``-0.0`` нормализуется к ``0.0``;
      - невалидные координаты пропускаются;
      - используется ``blake2b`` вместо ``md5``.
    """
    pts: list[tuple[float, float]] = []

    for coord in d.coords:
        try:
            lat_raw, lon_raw = coord
        except TypeError, ValueError:
            continue

        lat = _rounded_coordinate(lat_raw)
        lon = _rounded_coordinate(lon_raw)

        if lat is None or lon is None:
            continue

        pts.append((lat, lon))

    return hashlib.blake2b(
        repr(pts).encode("utf-8"),
        digest_size=5,
    ).hexdigest()[:10]
