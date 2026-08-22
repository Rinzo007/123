"""Типизированные результаты GIS-расчётов: GHS, POI, Built-S и Overture."""

from dataclasses import dataclass, field

__all__ = [
    "ADDITIVE_METRICS",
    "NON_ADDITIVE_METRICS",
    "BuiltSStats",
    "GhsStats",
    "OvertureStats",
    "PoiStats",
]

# Метрики, которые можно суммировать для независимых результатов.
ADDITIVE_METRICS = frozenset(
    {
        "volume_m3",
        "surface_m2",
        "total_area_m2",
        "total_value",
    }
)

# Метрики, которые зависят от конкретной геометрии/набора объектов
# и должны пересчитываться после изменения набора маршрутов.
NON_ADDITIVE_METRICS = frozenset(
    {
        "corridor_m2",
        "tiles_used",
        "count",
        "by_type",
    }
)


@dataclass(slots=True)
class GhsStats:
    """Результат расчёта объёма застройки GHS-BUILT-V."""

    volume_m3: float = 0.0
    corridor_m2: float = 0.0
    tiles_used: int = 0
    ok: bool = False


@dataclass(slots=True)
class PoiStats:
    """Результат расчёта ценности POI вдоль маршрута.

    ``by_type`` хранит количество объектов по категории POI.
    """

    total_value: float = 0.0
    count: int = 0
    by_type: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class BuiltSStats:
    """Результат расчёта площади застройки GHS-BUILT-S."""

    surface_m2: float = 0.0
    corridor_m2: float = 0.0
    tiles_used: int = 0
    ok: bool = False


@dataclass(slots=True)
class OvertureStats:
    """Результат расчёта площади объектов Overture."""

    total_area_m2: float = 0.0
    corridor_m2: float = 0.0
    ok: bool = False
