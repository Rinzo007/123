from __future__ import annotations

from dataclasses import dataclass
from math import exp


@dataclass(frozen=True, slots=True)
class PeriodTimetable:
    period_id: str
    departures_minute: tuple[float, ...]

    def __post_init__(self) -> None:
        if any(value < 0 or value >= 1440 for value in self.departures_minute):
            raise ValueError("Departure time must be within the day")
        if tuple(sorted(self.departures_minute)) != self.departures_minute:
            raise ValueError("Departure times must be sorted")


@dataclass(frozen=True, slots=True)
class ServiceTimetable:
    service_id: str
    periods: tuple[PeriodTimetable, ...]

    def for_period(self, period_id: str) -> PeriodTimetable | None:
        return next(
            (period for period in self.periods if period.period_id == period_id),
            None,
        )


def generate_service_timetable(
    service_id: str,
    period_windows: dict[str, tuple[int, int]],
    headway_by_period: dict[str, float],
    *,
    offset_minute: int = 0,
) -> ServiceTimetable:
    if offset_minute < 0 or offset_minute >= 1440:
        raise ValueError("offset_minute must be in [0, 1440)")

    result: list[PeriodTimetable] = []
    for period_id, (start_minute, end_minute) in period_windows.items():
        headway = headway_by_period.get(period_id)
        if headway is None:
            continue
        if headway <= 0:
            raise ValueError("Headways must be positive")
        if not 0 <= start_minute < end_minute <= 1440:
            raise ValueError("Invalid period window")

        first = start_minute + ((offset_minute - start_minute) % headway)
        departures: list[float] = []
        current = first
        while current < end_minute:
            departures.append(float(current))
            current += headway

        result.append(PeriodTimetable(period_id, tuple(departures)))

    return ServiceTimetable(service_id, tuple(result))


def next_departure(
    timetable: PeriodTimetable,
    arrival_minute: float,
) -> float | None:
    for departure in timetable.departures_minute:
        if departure >= arrival_minute:
            return departure
    return None


def wait_minutes(
    timetable: PeriodTimetable,
    arrival_minute: float,
) -> float | None:
    departure = next_departure(timetable, arrival_minute)
    if departure is None:
        return None
    return max(0.0, departure - arrival_minute)


def connection_wait(
    upstream_arrival_minute: float,
    downstream: PeriodTimetable,
) -> float | None:
    return wait_minutes(downstream, upstream_arrival_minute)



def average_connection_wait_minutes(
    upstream_headway: float,
    downstream_headway: float,
    *,
    upstream_offset: float = 0.0,
    downstream_offset: float = 0.0,
    upstream_run_time: float = 0.0,
    mode_jitter_s: float = 90.0,
    walk_time_min: float = 0.0,
) -> float | None:
    """Calculate schedule-aware average connection wait.

    This follows the reference worker's phase-aware connection rule.
    """
    if upstream_headway <= 0 or downstream_headway <= 0:
        return None
    if upstream_run_time < 0 or walk_time_min < 0 or mode_jitter_s < 0:
        raise ValueError("Connection timing inputs cannot be negative")

    ratio = upstream_headway / downstream_headway
    reverse_ratio = downstream_headway / upstream_headway
    if (
        abs(round(ratio) - ratio) > 1e-9
        and abs(round(reverse_ratio) - reverse_ratio) > 1e-9
    ):
        return downstream_headway / 2.0

    downstream_period_s = downstream_headway * 60.0
    upstream_period_s = upstream_headway * 60.0
    transfer_walk_s = walk_time_min * 60.0
    upstream_arrival_s = (upstream_offset + upstream_run_time) * 60.0
    downstream_phase_s = downstream_offset * 60.0
    jitter_s = max(
        20.0,
        (mode_jitter_s**2 + (0.4 * transfer_walk_s) ** 2) ** 0.5,
    )
    count = (
        1
        if upstream_period_s % downstream_period_s == 0
        else max(1, round(downstream_period_s / upstream_period_s))
    )

    total_wait_s = 0.0
    for index in range(count):
        phase_residual_s = (
            downstream_phase_s
            - (upstream_arrival_s + index * upstream_period_s)
        ) % downstream_period_s
        hold_probability = 1.0 / (
            1.0 + exp(
                -1.702 * phase_residual_s / max(1.0, jitter_s),
            )
        )
        total_wait_s += phase_residual_s + (
            1.0 - hold_probability
        ) * downstream_period_s
    return total_wait_s / count / 60.0
