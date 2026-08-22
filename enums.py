"""Перечисления типов транспорта и форматов экспорта."""

from enum import StrEnum

from .support import TYPE_LABELS, type_label

__all__ = [
    "TYPE_LABELS",
    "ExportFormat",
    "RouteType",
    "type_label",
]


class RouteType(StrEnum):
    """Поддерживаемые типы общественного транспорта."""

    TROLLEYBUS = "trolleybus"
    TRAM = "tram"
    WATER = "water"
    BUS = "bus"
    MINIBUS = "minibus"
    METRO = "metro"
    TRAIN = "train"
    FUNICULAR = "funicular"
    CABLE = "cable"
    ELECTROBUS = "electrobus"


class ExportFormat(StrEnum):
    """Поддерживаемые форматы экспортируемых результатов."""

    XLSX = "xlsx"
    KML = "kml"
