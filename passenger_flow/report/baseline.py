"""Сравнение расписаний с эталоном GTFS (baseline.json из пакета Takt).

Эталонный файл ``baseline.json`` содержит 2341 линию (bus/tram/metro/rail)
с интервалами по 5 периодам суток, координатами остановок и временами
проезда ``cumT``. Модуль читает этот формат, считает статистику интервалов
по периодам и типам транспорта и умеет сравнивать собственные расписания
с эталоном.

Пример::

    python -m wikiroutes.passenger_flow.baseline \\
        --baseline D:/Programs/page_assets/5ea07e08c0_baseline.json \\
        --seed D:/data/lines.json
"""

from __future__ import annotations

import argparse
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_PERIOD_SLOTS = 5
_EARTH_RADIUS_M = 6371000.0


@dataclass(frozen=True, slots=True)
class BaselineLine:
    """Одна линия эталона: имя, тип, интервалы по периодам, остановки, времена."""

    line_id: str
    name: str
    mode: str
    agency: str
    headways: tuple[float, ...]
    stops: tuple[tuple[float, float], ...]
    cum_t: tuple[float, ...]
    trips: float

    @property
    def runtime_min(self) -> float:
        """Время проезда маршрута (последний cumT), минуты."""
        return float(self.cum_t[-1] / 60.0) if self.cum_t else 0.0

    @property
    def length_km(self) -> float:
        """Длина маршрута по координатам остановок, км."""
        return sum(
            2.0
            * _EARTH_RADIUS_M
            * math.asin(
                math.sqrt(
                    math.sin((lat2 - lat1) * math.pi / 360.0) ** 2
                    + math.cos(lat1 * math.pi / 180.0)
                    * math.cos(lat2 * math.pi / 180.0)
                    * math.sin((lon2 - lon1) * math.pi / 360.0) ** 2
                )
            )
            for (lon1, lat1), (lon2, lat2) in zip(self.stops, self.stops[1:])
        ) / 1000.0

    @property
    def mean_speed_kmh(self) -> float:
        """Средняя коммерческая скорость маршрута, км/ч."""
        runtime_min = self.runtime_min
        if runtime_min <= 0.0:
            return 0.0
        return self.length_km / (runtime_min / 60.0)


def _to_headway_tuple(value: Any, slots: int = _PERIOD_SLOTS) -> tuple[float, ...]:
    if not value:
        return (0.0,) * slots
    items = [float(v) for v in value]
    if len(items) < slots:
        items = items + [items[-1]] * (slots - len(items))
    return tuple(items[:slots])


def load_baseline(path: str | Path) -> tuple[BaselineLine, ...]:
    """Читает ``baseline.json`` (ключи ``lines``/``generated``) из пакета Takt."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    lines = data.get("lines") if isinstance(data, dict) else data
    parsed: list[BaselineLine] = []
    for item in lines or []:
        stops = tuple(
            (float(s[0]), float(s[1]))
            for s in (item.get("stops") or [])
            if len(s) >= 2
        )
        if not stops:
            continue
        parsed.append(
            BaselineLine(
                line_id=str(item.get("id") or ""),
                name=str(item.get("name") or ""),
                mode=str(item.get("mode") or "").lower(),
                agency=str(item.get("agency") or ""),
                headways=_to_headway_tuple(item.get("headways")),
                stops=stops,
                cum_t=tuple(float(v) for v in (item.get("cumT") or ())),
                trips=float(item.get("trips") or 0.0),
            )
        )
    return tuple(parsed)


@dataclass(frozen=True, slots=True)
class HeadwayStats:
    """Медианный интервал по слотам для набора линий (0 — нет данных)."""

    median_slots: tuple[float, ...]
    mean_slots: tuple[float, ...]


def per_mode_headway_stats(
    lines: Sequence[BaselineLine],
) -> dict[str, HeadwayStats]:
    """Медианные/средние интервалы по 5 периодам, сгруппированные по типу."""
    grouped: dict[str, list[tuple[float, ...]]] = {}
    for line in lines:
        grouped.setdefault(line.mode, []).append(line.headways)
    result: dict[str, HeadwayStats] = {}
    for mode, headways in grouped.items():
        columns = list(zip(*headways))
        medians: list[float] = []
        means: list[float] = []
        for col in columns:
            non_zero = sorted(v for v in col if v > 0)
            if not non_zero:
                medians.append(0.0)
                means.append(0.0)
                continue
            n = len(non_zero)
            medians.append(
                non_zero[n // 2] if n % 2 else (non_zero[n // 2 - 1] + non_zero[n // 2]) / 2.0
            )
            means.append(sum(non_zero) / n)
        result[mode] = HeadwayStats(tuple(medians), tuple(means))
    return result


@dataclass(frozen=True, slots=True)
class ScheduleLine:
    """Собственное расписание линии для сравнения с эталоном."""

    mode: str
    name: str
    headways: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class BaselineComparison:
    """Результат сравнения расписаний с эталоном по слотам интервалов."""

    matched: tuple[tuple[BaselineLine, ScheduleLine], ...]
    unmatched_baseline: tuple[BaselineLine, ...]
    slot_rmse: tuple[float, ...]
    slot_mape: tuple[float, ...]


def compare_schedules(
    baseline: Sequence[BaselineLine],
    schedules: Sequence[ScheduleLine],
) -> BaselineComparison:
    """Сопоставляет линии эталона и собственные по ``(mode, name)``.

    ``slot_rmse`` — RMSE интервалов по каждому слота периода (мин),
    ``slot_mape`` — средняя абсолютная ошибка в %% (NaN, если в эталоне слот
    нулевой у всех сопоставленных линий).
    """
    ours: dict[tuple[str, str], ScheduleLine] = {
        (s.mode, s.name): s for s in schedules
    }
    matched: list[tuple[BaselineLine, ScheduleLine]] = []
    unmatched: list[BaselineLine] = []
    for line in baseline:
        key = (line.mode, line.name)
        if key in ours:
            matched.append((line, ours[key]))
        else:
            unmatched.append(line)
    errors: list[list[float]] = []
    rel_errors: list[list[float]] = []
    for ref, cur in matched:
        diffs = []
        rel = []
        for r, c in zip(
            _to_headway_tuple(ref.headways), _to_headway_tuple(cur.headways)
        ):
            if r <= 0.0:
                continue
            diffs.append((c - r) ** 2)
            rel.append(abs(c - r) / r * 100.0)
        errors.append(diffs)
        rel_errors.append(rel)
    slots = _PERIOD_SLOTS
    slot_rmse: list[float] = []
    slot_mape: list[float] = []
    for slot in range(slots):
        diffs = [e[slot] for e in errors if slot < len(e)]
        rel = [e[slot] for e in rel_errors if slot < len(e)]
        slot_rmse.append(
            math.sqrt(sum(diffs) / len(diffs)) if diffs else float("nan")
        )
        slot_mape.append(sum(rel) / len(rel) if rel else float("nan"))
    return BaselineComparison(
        matched=tuple(matched),
        unmatched_baseline=tuple(unmatched),
        slot_rmse=tuple(slot_rmse),
        slot_mape=tuple(slot_mape),
    )


def render_report(
    lines: Sequence[BaselineLine], comparison: BaselineComparison | None = None
) -> str:
    """Текстовый отчёт: агрегаты эталона и (при наличии) сравнение."""
    out: list[str] = []
    total_trips = sum(l.trips for l in lines)
    out.append(
        f"Эталон: линий {len(lines)}, поездок/сутки {total_trips:,.0f}"
    )
    stats = per_mode_headway_stats(lines)
    for mode, headway in sorted(stats.items()):
        out.append(
            f"  {mode:8s} линий {sum(1 for l in lines if l.mode == mode):5d}  "
            f"интервалы (мед.): "
            + " ".join(f"{v:4.1f}" if v else "  -- " for v in headway.median_slots)
        )
        speeds = [l.mean_speed_kmh for l in lines if l.mode == mode and l.mean_speed_kmh > 0]
        if speeds:
            out.append(f"           средняя скорость: {sum(speeds)/len(speeds):.1f} км/ч")
    if comparison is not None:
        out.append(
            f"Сравнение: сопоставлено {len(comparison.matched)} линий, "
            f"не нашлось в эталоне: {len(comparison.unmatched_baseline)}"
        )
        slots = ("early 04–06", "am 06–09", "mid 09–15", "pm 15–19", "eve 19–24")
        for i, (rmse, mape) in enumerate(zip(comparison.slot_rmse, comparison.slot_mape)):
            mape_s = f"{mape:6.1f}%" if not math.isnan(mape) else "   -- "
            rmse_s = f"{rmse:6.1f}" if not math.isnan(rmse) else "   -- "
            out.append(
                f"  период {slots[i]:14s} headway RMSE {rmse_s} мин, "
                f"MAPE {mape_s}"
            )
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="wikiroutes.baseline", description=__doc__
    )
    parser.add_argument("--baseline", required=True, help="Путь к baseline.json")
    parser.add_argument(
        "--seed",
        default=None,
        help="Файл собственных расписаний: JSON-список "
        '{mode, name, headways:<5 периодов>}',
    )
    args = parser.parse_args(argv)
    lines = load_baseline(args.baseline)
    comparison = None
    if args.seed:
        seed = json.loads(Path(args.seed).read_text(encoding="utf-8"))
        schedules = [
            ScheduleLine(
                mode=str(item.get("mode") or "").lower(),
                name=str(item.get("name") or ""),
                headways=_to_headway_tuple(item.get("headways")),
            )
            for item in (seed or [])
        ]
        comparison = compare_schedules(lines, schedules)
    print(render_report(lines, comparison))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())