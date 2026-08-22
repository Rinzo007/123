"""Общие вспомогательные функции для форматирования и терминалов."""

import math
import re
from typing import Any

from .compat import stop_lat, stop_lon, stop_name
from .constants import LAT_SHIFT, LON_SHIFT

__all__ = [
    "TYPE_LABELS",
    "clean_terminal_name",
    "collect_terminal_routes",
    "direction_endpoints",
    "fix_stop_coord",
    "fmt_curv_cell",
    "fmt_curv_str",
    "in_bbox",
    "normalize_terminal",
    "route_number",
    "route_passes_number_filter",
    "type_label",
]


TYPE_LABELS: dict[str, str] = {
    "trolleybus": "Троллейбус",
    "tram": "Трамвай",
    "water": "Водный транспорт",
    "bus": "Автобус",
    "minibus": "Маршрутное такси",
    "metro": "Метро",
    "train": "Поезд",
    "funicular": "Фуникулёр",
    "cable": "Канатная дорога",
    "electrobus": "Электробус",
}


def type_label(route_type: object) -> str:
    """Возвращает русскую метку типа транспорта.

    Принимает ``RouteType`` или строковое значение типа транспорта.
    """
    value = getattr(route_type, "value", route_type)
    if isinstance(value, str):
        return TYPE_LABELS.get(value, value)
    return str(route_type)


def fmt_curv_cell(value: float | None) -> float | str | None:
    """Форматирует коэффициент криволинейности для ячейки Excel."""
    if value is None:
        return None
    try:
        if math.isinf(value):
            return "∞"
        if math.isnan(value):
            return None
    except TypeError, ValueError:
        pass
    return round(value, 2)


def fmt_curv_str(value: float | None) -> str:
    """Форматирует коэффициент криволинейности в строку."""
    try:
        if value is None:
            return ""
        if math.isinf(value):
            return "∞"
        if math.isnan(value):
            return ""
    except TypeError, ValueError:
        pass
    return f"{value:.2f}"


def direction_endpoints(direction: object) -> tuple[str, str]:
    """Возвращает названия первой и последней остановок направления.

    Если список остановок недоступен, пытается разобрать ``direction.name``
    в формате ``«первая → последняя»``.
    """
    if direction is None:
        return "", ""
    stops = getattr(direction, "stops", None)
    if stops:
        first = stop_name(stops[0])
        last = stop_name(stops[-1])
        if first or last:
            return first, last
    name = getattr(direction, "name", "") or ""
    if "→" in name:
        first, last = name.split("→", 1)
        return first.strip(), last.strip()
    return "", ""


def in_bbox(
    lat: float | None,
    lon: float | None,
    bbox: tuple[float, float, float, float] | None,
) -> bool:
    """Проверяет, попадает ли точка в bbox."""
    if bbox is None:
        return False
    if lat is None or lon is None:
        return False
    if not (math.isfinite(lat) and math.isfinite(lon)):
        return False
    min_lat, min_lon, max_lat, max_lon = bbox
    return min_lat <= lat <= max_lat and min_lon <= lon <= max_lon


def fix_stop_coord(
    stop: object,
    index: int | None,
    bbox: tuple[float, float, float, float] | None,
) -> tuple[float, float] | None:
    """Возвращает корректные координаты остановки с учётом API-сдвига.

    Сначала используются исходные координаты, затем выполняется обратный сдвиг
    по позиционному индексу. При заданном bbox результат дополнительно проверяется
    на попадание в его границы.
    """
    lat = stop_lat(stop)
    lon = stop_lon(stop)
    if lat is None or lon is None:
        return None

    if in_bbox(lat, lon, bbox):
        return lat, lon

    idx = 0 if index is None else index
    lat2 = round(1e7 * (lat - LAT_SHIFT[idx & 7])) / 1e7
    lon2 = round(1e7 * (lon - LON_SHIFT[idx & 7])) / 1e7

    if in_bbox(lat2, lon2, bbox):
        return lat2, lon2

    if bbox is None and -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
        return lat, lon
    if -90.0 <= lat2 <= 90.0 and -180.0 <= lon2 <= 180.0:
        return lat2, lon2
    if -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
        return lat, lon
    return None


def normalize_terminal(name: str) -> str:
    """Нормализует название терминала для использования как ключ группировки."""
    if not name:
        return ""
    text = re.sub(r"[«»\"'’`]", "", str(name).strip().lower())
    text = re.sub(r"\s+", " ", text)
    return text.strip(" ,;:!?-.")


def clean_terminal_name(name: str) -> str:
    """Удаляет из названия терминала примечания в скобках, включая вложенные."""
    text = str(name or "")
    previous = None
    while previous != text:
        previous = text
        text = re.sub(r"\([^()]*\)", "", text)
    return re.sub(r"\s+", " ", text).strip()


def route_number(name: str) -> int | None:
    """Возвращает номер маршрута — первую группу цифр в названии.

    Примеры: ``"11"`` → 11, ``"э11"`` → 11, ``"11а"`` → 11, ``"б/н"`` → None.
    """
    match = re.search(r"\d+", str(name or ""))
    return int(match.group()) if match else None


def route_passes_number_filter(name: str, max_route_number: int) -> bool:
    """True, если маршрут проходит фильтр ``--max-route-number``.

    При активном фильтре исключаются маршруты без номера, с номером больше
    лимита и маршруты со скобками в названии.
    """
    if max_route_number <= 0:
        return True

    if "(" in name or ")" in name:
        return False

    num = route_number(name)
    return num is not None and num <= max_route_number


def collect_terminal_routes(routes: list[Any]) -> dict[str, dict[str, Any]]:
    """Группирует маршруты по нормализованным названиям конечных остановок."""
    terminals: dict[str, dict[str, Any]] = {}

    for route in routes:
        if getattr(route, "error", None) or not getattr(route, "directions", None):
            continue

        seen: set[str] = set()

        for direction in route.directions:
            stops = getattr(direction, "stops", None)
            if not stops:
                continue

            for stop in (stops[0], stops[-1]):
                raw_name = stop_name(stop)
                cleaned_name = clean_terminal_name(raw_name)
                key = normalize_terminal(cleaned_name)

                if not key or key in seen:
                    continue

                seen.add(key)
                record = terminals.setdefault(
                    key,
                    {"name": cleaned_name, "routes": []},
                )
                record["routes"].append(route)

    return terminals
