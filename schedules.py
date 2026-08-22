"""Разбор расписаний WikiRoutes."""

import re
from collections.abc import Mapping, Sequence
from typing import Any

from .models import ScheduleSummary


def parse_hhmm(value: Any) -> int | None:
    """Преобразует строку ``HH:MM`` в минуты от начала суток.

    Возвращает ``None`` для значений другого типа или недопустимого времени.
    Текущий формат намеренно ограничен диапазоном ``00:00``—``23:59``.
    """
    if not isinstance(value, str):
        return None

    match = re.fullmatch(r"(\d{1,2}):(\d{2})", value.strip())
    if match is None:
        return None

    hours = int(match[1])
    minutes = int(match[2])

    if hours > 23 or minutes > 59:
        return None

    return hours * 60 + minutes


def _weekday_enabled(value: Any) -> bool:
    """Нормализует bool/числовые/string-флаги weekday без ``bool("0")``."""
    if isinstance(value, bool):
        return value

    if isinstance(value, int | float):
        return value != 0

    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"", "0", "false", "no", "off", "none"}:
            return False
        if normalized in {"1", "true", "yes", "on"}:
            return True

    return bool(value)


def _has_enabled_day(values: Sequence[Any], indices: tuple[int, ...]) -> bool:
    """Проверяет, включён ли хотя бы один день из заданных индексов."""
    return any(
        _weekday_enabled(values[index]) for index in indices if index < len(values)
    )


def summarize_schedules(payload: Mapping[str, Any]) -> ScheduleSummary | None:
    """Суммирует время отправлений и дни работы из payload WikiRoutes.

    Возвращает первый и последний рейс, число рейсов по будням и выходным,
    приблизительный интервал между рейсами и текстовое описание режима работы.
    Если валидных отправлений нет, возвращает ``None``.
    """
    times: list[int] = []
    weekday_trips = 0
    weekend_trips = 0

    trips = payload.get("trips") or []
    if not isinstance(trips, list):
        return None

    for trip in trips:
        if not isinstance(trip, Mapping):
            continue

        schedules = trip.get("schedules") or []
        if not isinstance(schedules, list):
            continue

        for schedule in schedules:
            if not isinstance(schedule, Mapping):
                continue

            weekdays_raw = schedule.get("weekDays") or []
            if isinstance(weekdays_raw, str) or not isinstance(weekdays_raw, Sequence):
                weekdays: Sequence[Any] = ()
            else:
                weekdays = weekdays_raw

            departure_times = schedule.get("departureTimes") or []
            if not isinstance(departure_times, list):
                continue

            minutes: list[int] = []
            for item in departure_times:
                if not isinstance(item, Mapping):
                    continue

                parsed = parse_hhmm(
                    item.get("actualDepartureTime") or item.get("sourceTime")
                )
                if parsed is not None:
                    minutes.append(parsed)

            times.extend(minutes)

            if _has_enabled_day(weekdays, (0, 1, 2, 3, 4)):
                weekday_trips += len(minutes)

            if _has_enabled_day(weekdays, (5, 6)):
                weekend_trips += len(minutes)

    if not times:
        return None

    first = min(times)
    last = max(times)
    denominator = weekday_trips or len(times)
    head = (last - first) / max(1, denominator - 1) if denominator > 1 else 0.0

    if weekday_trips and weekend_trips:
        days = "ежедневно"
    elif weekday_trips:
        days = "будни"
    elif weekend_trips:
        days = "выходные"
    else:
        days = ""

    return ScheduleSummary(
        first=first,
        last=last,
        weekday_trips=weekday_trips,
        weekend_trips=weekend_trips,
        head_minutes=head,
        days=days,
    )
