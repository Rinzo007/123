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
) -> int | None:
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
