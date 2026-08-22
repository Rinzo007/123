from collections.abc import Mapping
from typing import Any

from .decoding import unshift_coords
from .models import Direction, ParsedRoute, Stop
from .schedules import summarize_schedules


def parse_route_payload(payload: Any) -> ParsedRoute | None:
    """Преобразует декодированный payload WikiRoutes в доменную модель маршрута.

    Повторяющиеся геометрии направлений удаляются по равенству кортежей
    координат (хэш кортежа вместо дорогой SHA-256 по строке).
    Некорректные записи и маршруты без валидной геометрии пропускаются.
    """
    if not isinstance(payload, Mapping):
        return None

    trips = payload.get("trips") or []

    if not isinstance(trips, list):
        trips = []

    directions: list[Direction] = []
    seen: set[tuple[Any, ...]] = set()

    for trip in trips:
        if not isinstance(trip, Mapping):
            continue

        line = trip.get("line") or {}

        if not isinstance(line, Mapping):
            continue

        coords = unshift_coords(line.get("coordinates"))

        if not coords:
            continue

        raw_stops = line.get("stops") or []
        stops: list[Stop] = []

        if isinstance(raw_stops, list):
            for item in raw_stops:
                if not isinstance(item, Mapping):
                    continue

                stop = Stop.from_api(dict(item))

                if stop is not None:
                    stops.append(stop)

        if coords in seen:
            continue

        seen.add(coords)

        name = ""

        if stops:
            first = stops[0].name or "?"
            last = stops[-1].name or "?"
            name = f"{first} → {last}"

        directions.append(
            Direction(
                coords=coords,
                stops=tuple(stops),
                name=name,
            )
        )

    if not directions:
        return None

    return ParsedRoute(
        directions=tuple(directions),
        price=str(payload.get("price") or ""),
        company=str(payload.get("company") or ""),
        active=bool(payload.get("active", True)),
        schedule=summarize_schedules(payload),
        transport_class=str(payload.get("transport_class") or ""),
    )
