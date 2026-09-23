"""Общие геометрические помощники GHS.

Пакет содержит переиспользуемые примитивы, необходимые пространственным
стадиям. Тяжёлые расчёты GHS-BUILT-V/S загружаются отдельно и лениво.
"""

from .buffer import STOP_GEO_MAX_DIST_M, _filter_stops_near_route

__all__ = ["STOP_GEO_MAX_DIST_M", "_filter_stops_near_route"]
