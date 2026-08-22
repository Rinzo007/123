"""Публичная точка входа пакета."""

from .cli import main
from .enums import RouteType
from . import pipeline as _pipeline

# В единой модели все типы маршрутов загружаются одинаково.
# Имя BASE_ROUTE_TYPES сохраняем только как compatibility alias для внешнего кода.
_pipeline.BASE_ROUTE_TYPES = frozenset(RouteType)

__all__ = ["main"]
