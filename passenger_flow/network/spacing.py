"""Проверка межостановочных расстояний по модели Takt (playtakt.app).

Диапазоны «рациональных» расстояний между соседними остановками по типам
транспорта и функция «вердикта» для заданной плотности застройки:
bus 300–500 м, tram 400–700 м, metro 800–1500 м, rail 2000–5000 м.

Чем плотнее застройка, тем диапазон сужается: частые остановки оправданы
рядом с генераторами поездок, редкие — на периферии.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise

from ..base.takt import (
    _SPACING_DENSE_PREFIX,
    _SPACING_DENSITY_BASE,
    _SPACING_DENSITY_SPAN,
    _SPACING_SHRINK_HI,
    _SPACING_SHRINK_LO,
    _SPACING_THIN_PREFIX,
    _SPACING_TOO_CLOSE_FACTOR,
    _SPACING_WIDE_FACTOR,
    STOP_SPACING_BANDS,
)
from .geometry import haversine_meters

_MODE_KEYS = {
    "bus": "bus",
    "trolleybus": "bus",
    "electrobus": "bus",
    "minibus": "bus",
    "water": "bus",
    "tram": "tram",
    "metro": "metro",
    "monorail": "metro",
    "rail": "rail",
    "train": "rail",
    "railroad": "rail",
}

_SPACING_WHY: dict[str, str] = {
    "too close": (
        "Две остановки так близко обслуживают одних и тех же пассажиров "
        "дважды: каждая всё равно стоит времени посадки на каждом рейсе."
    ),
    "tight": (
        "Ближе стандартного диапазона: оправдано рядом с генератором "
        "поездок, иначе расточительно."
    ),
    "good": (
        "{prefix}Оптимально для этого вида транспорта: достаточно близко, "
        "чтобы дойти пешком, и достаточно далеко, чтобы сохранять скорость."
    ),
    "wide": (
        "Шире стандартного диапазона: пассажиру в середине перегона долго "
        "идти до любого его конца."
    ),
    "too far": (
        "Слишком длинный перегон: середина остаётся необслуженной. "
        "Нужна промежуточная остановка."
    ),
}


@dataclass(frozen=True, slots=True)
class StopSpacingVerdict:
    """Оценка одного межостановочного расстояния.

    ``verdict`` — ``too close`` / ``tight`` / ``good`` / ``wide`` /
    ``too far``; ``tone`` — ``bad`` / ``warn`` / ``good`` для подсветки;
    ``lo``/``hi`` — суженные под плотность границы диапазона.
    """

    verdict: str
    tone: str
    lo: int
    hi: int
    why: str


def stop_spacing_band(mode: object) -> tuple[int, int]:
    """Диапазон расстояний (м) для типа транспорта."""
    key = _MODE_KEYS.get(str(mode).strip().lower(), "bus")
    return STOP_SPACING_BANDS[key]


def stop_spacing_verdict(
    mode: object,
    dist_m: float,
    density: float = 0.5,
) -> StopSpacingVerdict:
    """Вердикт для межостановочного расстояния ``dist_m`` (м).

    ``density`` — плотность застройки [0, 1]: чем выше, тем уже допустимый
    диапазон (частые остановки в многолюдных кварталах оправданы).
    """
    lo, hi = stop_spacing_band(mode)
    dens = min(1.0, max(0.0, float(density)))
    tightness = max(0.0, dens - _SPACING_DENSITY_BASE) / _SPACING_DENSITY_SPAN
    lo_shrunk = round(lo * (1.0 - _SPACING_SHRINK_LO * tightness))
    hi_shrunk = round(hi * (1.0 - _SPACING_SHRINK_HI * tightness))
    dist = round(float(dist_m))

    if dist < lo_shrunk * _SPACING_TOO_CLOSE_FACTOR:
        verdict, tone = "too close", "bad"
    elif dist < lo_shrunk:
        verdict, tone = "tight", "warn"
    elif dist <= hi_shrunk:
        verdict, tone = "good", "good"
    elif dist <= hi_shrunk * _SPACING_WIDE_FACTOR:
        verdict, tone = "wide", "warn"
    else:
        verdict, tone = "too far", "bad"

    if dens > _SPACING_DENSE_PREFIX:
        prefix = "Плотная застройка, и "
    elif dens < _SPACING_THIN_PREFIX:
        prefix = "Разреженная застройка, и "
    else:
        prefix = ""
    return StopSpacingVerdict(
        verdict=verdict,
        tone=tone,
        lo=lo_shrunk,
        hi=hi_shrunk,
        why=_SPACING_WHY[verdict].format(prefix=prefix),
    )


def check_stop_spacing(
    stops: list[dict[str, object]],
    mode: object,
    density: float = 0.5,
) -> list[tuple[float, StopSpacingVerdict]]:
    """Вердикты для всех соседних пар остановок (lat/lon из dict).

    Возвращает ``[(dist_m, verdict)]`` для последовательных остановок;
    пустой список при менее двух остановок.
    """
    pairs: list[tuple[float, StopSpacingVerdict]] = []
    for a, b in pairwise(stops):
        lat_a = float(a["lat"])
        lon_a = float(a["lon"])
        lat_b = float(b["lat"])
        lon_b = float(b["lon"])
        dist_m = haversine_meters(lat_a, lon_a, lat_b, lon_b)
        pairs.append((dist_m, stop_spacing_verdict(mode, dist_m, density)))
    return pairs