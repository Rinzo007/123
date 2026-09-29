"""Якоряние оценок к контрольным итогам.

Оценка снизу вверх (жилая площадь зданий) и официальный итог (перепись,
занятость по ведомству) почти никогда не совпадают: различаются и полнота
покрытия, и год, и границы. Правильный порядок такой: форму распределения
берём из оценки снизу вверх, а величину - из контрольного итога.

Это ровно то, чем отличается калиброванная модель от неоткалиброванной:
не переписываются пропорции, масштабируется всё сразу. Зоны с нулевой оценкой
при ненулевом контрольном итоге остаются нулевыми - и это видно в отчёте,
потому что иначе молчаливый ноль выглядит как «там никого нет».
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ZoneControl:
    """Контрольный итог по одной зоне с указанием источника и даты."""

    zone_id: str
    population: float
    jobs: float = 0.0
    source: str = ""
    vintage: str = ""


@dataclass(frozen=True, slots=True)
class AnchoringReport:
    """Что произошло при якорянии - попадает в provenance пака."""

    method: str
    total_estimated: float
    total_control: float
    factor: float
    zones_scaled: int
    zones_zero_estimate_but_control: tuple[str, ...]
    zones_zero_control_but_estimate: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "method": self.method,
            "totalEstimated": round(self.total_estimated, 3),
            "totalControl": round(self.total_control, 3),
            "factor": round(self.factor, 6),
            "zonesScaled": self.zones_scaled,
            "zonesZeroEstimateButControl": list(self.zones_zero_estimate_but_control),
            "zonesZeroControlButEstimate": list(self.zones_zero_control_but_estimate),
        }


def anchor_to_control(
    estimates: dict[str, float],
    controls: tuple[ZoneControl, ...],
) -> tuple[dict[str, float], AnchoringReport]:
    """Масштабирует оценки зон к суммарному контрольному итогу.

    Пропорции внутри сохраняются: ``estimate / total_estimate * total_control``.
    Зоны, которым оценка не дала ничего, остаются нулевыми и перечисляются в
    отчёте - иначе контрольный итог распределился бы по ним молча.
    """
    if any(value < 0 for value in estimates.values()):
        raise ValueError("estimates must be non-negative")
    if any(control.population < 0 or control.jobs < 0 for control in controls):
        raise ValueError("control totals must be non-negative")
    if not controls:
        return dict(estimates), AnchoringReport(
            method="none", total_estimated=sum(estimates.values()), total_control=0.0,
            factor=0.0, zones_scaled=0, zones_zero_estimate_but_control=(),
            zones_zero_control_but_estimate=(),
        )

    total_estimated = sum(estimates.values())
    total_control = sum(control.population for control in controls)
    factor = (total_control / total_estimated) if total_estimated > 0 else 0.0

    control_by_zone = {control.zone_id: control for control in controls}
    anchored = {
        zone_id: (value * factor if total_estimated > 0 else 0.0)
        for zone_id, value in estimates.items()
    }

    zero_estimate = tuple(
        control.zone_id
        for control in controls
        if estimates.get(control.zone_id, 0.0) <= 0 and control.population > 0
    )
    zero_control = tuple(
        zone_id
        for zone_id, value in estimates.items()
        if value > 0 and control_by_zone.get(zone_id, ZoneControl(zone_id, 0.0)).population <= 0
    )

    return anchored, AnchoringReport(
        method="scale_to_control_total",
        total_estimated=total_estimated,
        total_control=total_control,
        factor=factor,
        zones_scaled=sum(1 for value in anchored.values() if value > 0),
        zones_zero_estimate_but_control=zero_estimate,
        zones_zero_control_but_estimate=zero_control,
    )


def anchor_jobs_to_control(
    estimates: dict[str, float],
    controls: tuple[ZoneControl, ...],
) -> tuple[dict[str, float], AnchoringReport]:
    """То же для рабочих мест: сумма контролей берётся по jobs."""
    if any(value < 0 for value in estimates.values()):
        raise ValueError("estimates must be non-negative")
    if any(control.jobs < 0 for control in controls):
        raise ValueError("control jobs must be non-negative")
    total_estimated = sum(estimates.values())
    total_control = sum(control.jobs for control in controls)
    factor = (total_control / total_estimated) if total_estimated > 0 else 0.0
    control_by_zone = {control.zone_id: control for control in controls}
    anchored = {
        zone_id: (value * factor if total_estimated > 0 else 0.0)
        for zone_id, value in estimates.items()
    }
    zero_estimate = tuple(
        control.zone_id
        for control in controls
        if estimates.get(control.zone_id, 0.0) <= 0 and control.jobs > 0
    )
    zero_control = tuple(
        zone_id
        for zone_id, value in estimates.items()
        if value > 0 and control_by_zone.get(zone_id, ZoneControl(zone_id, 0.0)).jobs <= 0
    )
    return anchored, AnchoringReport(
        method="scale_to_control_total",
        total_estimated=total_estimated,
        total_control=total_control,
        factor=factor,
        zones_scaled=sum(1 for value in anchored.values() if value > 0),
        zones_zero_estimate_but_control=zero_estimate,
        zones_zero_control_but_estimate=zero_control,
    )
