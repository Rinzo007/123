from __future__ import annotations

from dataclasses import dataclass


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
) -> float | None:
    """Average downstream wait for periodic upstream arrivals.

    When the two headways are commensurate, evaluate the exact repeating
    departure pattern. Otherwise use the stationary half-headway expectation.
    """
    if upstream_headway <= 0 or downstream_headway <= 0:
        return None
    if upstream_run_time < 0:
        raise ValueError("upstream_run_time cannot be negative")

    ratio = upstream_headway / downstream_headway
    reverse_ratio = downstream_headway / upstream_headway
    if abs(round(ratio) - ratio) > 1e-9 and abs(round(reverse_ratio) - reverse_ratio) > 1e-9:
        return downstream_headway / 2.0

    cycle = max(upstream_headway, downstream_headway)
    if abs(ratio - round(ratio)) <= 1e-9:
        cycle *= 1.0
    else:
        cycle *= round(reverse_ratio)
    count = max(1, int(round(cycle / upstream_headway)))
    waits = []
    for index in range(count):
        arrival = upstream_offset + index * upstream_headway + upstream_run_time
        next_departure = downstream_offset + (
            (arrival - downstream_offset + downstream_headway - 1e-12)
            // downstream_headway
        ) * downstream_headway
        if next_departure < arrival - 1e-9:
            next_departure += downstream_headway
        waits.append(max(0.0, next_departure - arrival))
    return sum(waits) / len(waits)
