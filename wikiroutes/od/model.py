"""Базовые типы, ошибки и математические примитивы OD-модуля.

Общие идентификаторы без внутренних зависимостей: классы-данные результата,
исключение ``OdMatrixError``, гаверсинус-расстояние, местный масштаб долготы
для евклидовых NN и округление половиной вверх (как ``Math.round`` движка JS).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy import sparse

from passenger_flow.models import Period, Purpose

__all__ = [
    "_KILOMETERS_PER_DEGREE",
    "OdMatrixError",
    "OdResult",
    "PurposeOd",
    "TaktDemand",
    "TaktPurposeLayer",
    "TaktPurposes",
    "Zones",
    "_as_sparse",
    "_cosscale",
    "_round_half_up",
    "haversine_km",
]

_KILOMETERS_PER_DEGREE = 111.32


class OdMatrixError(RuntimeError):
    """Ошибка вычисления матрицы корреспонденций."""


@dataclass(frozen=True, slots=True)
class Zones:
    """Сетка транспортных зон: id, полигоны, центроиды и общий bbox."""

    ids: np.ndarray
    polygons: tuple[Any, ...]
    xy: np.ndarray
    bounds: tuple[float, float, float, float]

    def __len__(self) -> int:
        return int(self.ids.shape[0])


@dataclass(frozen=True, slots=True)
class OdResult:
    """Результат расчёта матрицы корреспонденций."""

    zones: Zones
    matrix: np.ndarray
    costs: np.ndarray
    weights: np.ndarray
    road_ways: int
    euclidean: bool
    road_loads: list[tuple[Any, Any, float]] | None = None
    sparse_matrix: sparse.csr_matrix | None = None
    purpose_matrices: tuple[np.ndarray, ...] | None = None
    purpose_periods: tuple[Period, ...] = ()
    purpose_trips: tuple[float, ...] = ()
    purpose_keys: tuple[str, ...] = ()
    district_names: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PurposeOd:
    """Итог генерации OD по целям поездок (Takt-подобное тяготение).

    ``matrix`` — суммарная матрица, ``purpose_matrices`` — по целям в том же
    порядке, что ``purposes`` (CSR для движка Takt, плотные для gravity);
    ``period_out``/``period_ret`` — взвешенные по
    долям поездок профили периодов суток; ``shares`` — доля каждой цели;
    ``purpose_base_times`` — сохранённые времена ``baseT`` по целям;
    ``commute_base_time`` — сохранённый ``commuteBaseT``.
    """

    matrix: np.ndarray
    purpose_matrices: tuple[np.ndarray | sparse.csr_matrix, ...]
    period_out: tuple[float, ...]
    period_ret: tuple[float, ...]
    shares: tuple[float, ...]
    purposes: tuple[Purpose, ...]
    purpose_base_times: tuple[np.ndarray | None, ...] = ()
    commute_base_time: np.ndarray | None = None


def _as_sparse(matrix: np.ndarray) -> sparse.csr_matrix:
    """Возвращает CSR-представление матрицы OD (только ненулевые пары)."""
    return sparse.csr_matrix(matrix, dtype=np.float64)


@dataclass(frozen=True, slots=True)
class TaktDemand:
    """Готовый спрос из пакета Takt: точки спроса и матрица OD.

    ``points`` — таблица ``[lon, lat, pop, jobs]``; ``production`` — население
    точек; ``matrix`` — поездки между точками из раздела ``od``.
    """

    zones: Zones
    matrix: np.ndarray
    production: np.ndarray
    points: np.ndarray


@dataclass(frozen=True, slots=True)
class TaktPurposeLayer:
    """Слой целей поездок из ``purposes.bin.json``.

    ``key`` — код цели (edu/health/shop/air/night); ``out``/``ret`` — профили
    периодов суток (доли, сумма 1.0); ``pairs`` — массив
    ``[origin, dest, trips, seconds]``; ``base_time`` — базовое время поездки
    по периодам (``[период, пара]``), NaN при отсутствии.
    """

    key: str
    out: tuple[float, ...]
    ret: tuple[float, ...]
    pairs: np.ndarray
    base_time: np.ndarray | None


@dataclass(frozen=True, slots=True)
class TaktPurposes:
    """Набор слоёв целей из ``purposes.bin.json``.

    ``layers`` — слои в порядке файла; ``commute_base_time`` — базовое время
    поездки на работу по периодам (опционально). ``n_periods`` — число
    периодов суток, общее для всех слоёв.
    """

    layers: tuple[TaktPurposeLayer, ...]
    commute_base_time: np.ndarray | None = None

    @property
    def n_periods(self) -> int:
        return len(self.layers[0].out) if self.layers else 0


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Расстояние между точками ``(lon, lat)`` по формуле гаверсинуса, км."""
    lat1, lon1 = math.radians(a[1]), math.radians(a[0])
    lat2, lon2 = math.radians(b[1]), math.radians(b[0])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = (
        math.sin(dlat / 2.0) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2.0) ** 2
    )
    return 6371.0 * 2.0 * math.asin(math.sqrt(h))


def _cosscale(*points: np.ndarray) -> float:
    """cos(средней широты) — местный масштаб долготы для евклидовых NN.

    Bез масштаба cKDTree ищет соседей в декартовых градусах, где 1° долготы
    короче 1° широты в ``cos(lat)`` раз; для городов с широтами ≲60° хвост
    ошибки достигает сотен метров даже в пределах агломерации.
    """
    latitudes = np.concatenate([np.asarray(p)[:, 1] for p in points]) if points else np.empty(0)
    if latitudes.size == 0:
        return 1.0
    return float(np.cos(np.deg2rad(np.mean(latitudes))))


def _round_half_up(value: float) -> int:
    """Округление как ``Math.round`` движка JS (половина — вверх)."""
    return math.floor(value + 0.5)