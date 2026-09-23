"""Калибровка интервалов движения под эталонный изначальный `baseline.json`.

Покомпонентный (покоординатный) спуск по интервалу каждой линии: для пары
``(mode, name)`` подбирается единый интервал, минимизирующий средний
квадрат отклонения от эталонных интервалов по периодам суток. Для одной
скалярной переменной оптимум совпадает со средним эталонов по слотам,
поэтому алгоритм состоит из детерминированного шага за шагом нахождения
оптимума с ограничением диапазона.

Пример::

    python -m wikiroutes.passenger_flow.tune \\
        --baseline D:/Programs/page_assets/5ea07e08c0_baseline.json \\
        --routes lines.json --out tuned.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .baseline import (
    _PERIOD_SLOTS,
    BaselineLine,
    ScheduleLine,
    _to_headway_tuple,
    load_baseline,
)

_MIN_HEADWAY = 2.0
_MAX_HEADWAY = 60.0


@dataclass(frozen=True, slots=True)
class TunedLine:
    """Откалиброванный интервал линии и ошибка до/после калибровки."""

    mode: str
    name: str
    headway: float
    rmse_before: float
    rmse_after: float


@dataclass(slots=True)
class TuneResult:
    """Результат калибровки: интервалы по линиям и сводная ошибка."""

    tuned: tuple[TunedLine, ...] = field(default_factory=tuple)
    unmatched: tuple[BaselineLine, ...] = field(default_factory=tuple)

    @property
    def rmse_after(self) -> float:
        if not self.tuned:
            return 0.0
        return math.sqrt(
            sum(l.rmse_after**2 for l in self.tuned) / len(self.tuned)
        )

    @property
    def rmse_before(self) -> float:
        if not self.tuned:
            return 0.0
        return math.sqrt(
            sum(l.rmse_before**2 for l in self.tuned) / len(self.tuned)
        )

    @property
    def headway_map(self) -> dict[int | str, float]:
        """{(mode, name): headway} для передачи в ``run_passenger_flow``."""
        return {(l.mode, l.name): l.headway for l in self.tuned}

    def as_json(self) -> list[dict[str, Any]]:
        return [
            {
                "mode": l.mode,
                "name": l.name,
                "headway_min": l.headway,
            }
            for l in sorted(self.tuned, key=lambda x: (x.mode, x.name))
        ]


def _line_rmse(
    ref: Sequence[float], cur: Sequence[float]
) -> float:
    """RMSE интервалов по слотам (непустые эталонные слоты), мин."""
    diffs = [
        (b - c) ** 2
        for b, c in zip(ref, cur)
        if b > 0
    ]
    if not diffs:
        return 0.0
    return math.sqrt(sum(diffs) / len(diffs))


def _round_significant(value: float) -> float:
    """Округляет до двух значащих цифр (шаг калибровки остаётся осмысленным)."""
    if value == 0.0:
        return 0.0
    decimals = max(0, 1 - math.floor(math.log10(abs(value))))
    return round(value, decimals)


def _brief_clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _coordinate_descent(
    ref: Sequence[float],
    start: float,
    lo: float,
    hi: float,
    iters: int = 12,
) -> tuple[float, float, float]:
    """Покоординатный спуск по одной скалярной переменной headway.

    Возвращает ``(headway, rmse_before, rmse_after)``. Для квадратичной
    функции по одной координате итерация гарантированно сходится к среднему
    эталонов, ограниченному диапазоном.
    """
    base = [float(v) for v in ref]
    base = [v for v in base if v > 0]
    if not base:
        mean = start
    else:
        mean = sum(base) / len(base)
    optimum = _brief_clamp(mean, lo, hi)
    # Несколько шагов упорядоченного спуска в обе стороны от среднего,
    # чтобы учесть дискретность диапазона и стабилизировать результат.
    best = optimum
    best_err = _line_rmse(ref, (optimum,) * _PERIOD_SLOTS)
    for _ in range(iters):
        step = max((hi - lo) / 16.0, 0.5)
        improved = False
        for cand in (best - step, best + step):
            if not (lo <= cand <= hi):
                continue
            err = _line_rmse(ref, (cand,) * _PERIOD_SLOTS)
            if err < best_err:
                best, best_err = cand, err
                improved = True
        if not improved:
            break
        best = _brief_clamp(best, lo, hi)
    before = _line_rmse(ref, (start,) * _PERIOD_SLOTS)
    return _round_significant(best), before, _round_significant(best_err)


def tune_headways(
    baseline: Sequence[BaselineLine],
    schedules: Sequence[ScheduleLine],
    *,
    min_headway: float = _MIN_HEADWAY,
    max_headway: float = _MAX_HEADWAY,
) -> TuneResult:
    """Подбирает интервал каждой собственной линии под эталон.

    Сопоставление по ``(mode, name)``; линии без пары в эталоне остаются
    без изменений и не попадают в результат. Интервал ограничивается
    ``[min_headway, max_headway]`` и округляется до двух значащих цифр.
    """
    if min_headway <= 0 or max_headway <= min_headway:
        raise ValueError("min_headway должен быть положительным и меньше max_headway")
    baseline_by_key: dict[tuple[str, str], BaselineLine] = {
        (l.mode, l.name): l for l in baseline
    }
    tuned: list[TunedLine] = []
    for sch in schedules:
        key = (sch.mode, sch.name)
        ref = baseline_by_key.get(key)
        if ref is None:
            continue
        ref_headways = _to_headway_tuple(ref.headways)
        cur_headways = _to_headway_tuple(sch.headways)
        if any(v > 0 for v in cur_headways):
            start = sum(cur_headways) / len(cur_headways)
        else:
            start = _MIN_HEADWAY
        headway, rmse_before, rmse_after = _coordinate_descent(
            ref_headways, start, min_headway, max_headway
        )
        if rmse_before == 0.0:
            rmse_before = _line_rmse(ref_headways, cur_headways)
        tuned.append(
            TunedLine(
                mode=sch.mode,
                name=sch.name,
                headway=headway,
                rmse_before=rmse_before,
                rmse_after=rmse_after,
            )
        )
    return TuneResult(
        tuned=tuple(tuned),
        unmatched=tuple(
            l for l in baseline if (l.mode, l.name) not in {(s.mode, s.name) for s in schedules}
        ),
    )


def render_tune_report(result: TuneResult) -> str:
    """Текстовый отчёт о калибровке интервалов."""
    out: list[str] = []
    out.append(f"Откалибровано линий: {len(result.tuned)}")
    if result.tuned:
        out.append(
            f"  RMSE до: {result.rmse_before:.2f} мин, после: {result.rmse_after:.2f} мин"
        )
        by_mode: dict[str, list[TunedLine]] = {}
        for line in result.tuned:
            by_mode.setdefault(line.mode, []).append(line)
        for mode, lines in sorted(by_mode.items()):
            hw = [l.headway for l in lines]
            out.append(
                f"  {mode:8s} линий {len(lines):5d}  "
                f"headway: от {min(hw):.1f} до {max(hw):.1f} мин "
                f"(среднее {sum(hw) / len(hw):.1f})"
            )
    if result.unmatched:
        out.append(f"Не сопоставлено с эталоном: {len(result.unmatched)} линий")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="wikiroutes.tune", description=__doc__
    )
    parser.add_argument("--baseline", required=True, help="Путь к baseline.json Takt")
    parser.add_argument(
        "--routes",
        required=True,
        help="Собственные расписания: JSON-список {mode, name, headways} либо "
        "путь к выходу pipeline (dict с ключом routelines)",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Файл для результата {mode, name, headway_min} в JSON",
    )
    parser.add_argument(
        "--min-headway", type=float, default=_MIN_HEADWAY,
        help=f"Нижняя граница интервала, мин (по умолчанию {_MIN_HEADWAY})",
    )
    parser.add_argument(
        "--max-headway", type=float, default=_MAX_HEADWAY,
        help=f"Верхняя граница интервала, мин (по умолчанию {_MAX_HEADWAY})",
    )
    args = parser.parse_args(argv)

    baseline = load_baseline(args.baseline)
    raw = json.loads(Path(args.routes).read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = raw.get("routelines") or raw.get("lines") or []
    schedules = [
        ScheduleLine(
            mode=str(item.get("mode") or "").lower(),
            name=str(item.get("name") or ""),
            headways=_to_headway_tuple(item.get("headways")),
        )
        for item in raw
    ]
    result = tune_headways(
        baseline, schedules,
        min_headway=args.min_headway, max_headway=args.max_headway,
    )
    print(render_tune_report(result))

    if args.out:
        out_path = Path(args.out)
        payload: dict[str, object] = {
            "lines": result.as_json(),
            "rmse_before_min": round(result.rmse_before, 2),
            "rmse_after_min": round(result.rmse_after, 2),
        }
        out_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"  Результат сохранён: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))