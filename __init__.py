"""Публичная точка входа пакета."""

from .cli import main
from .enums import RouteType
from . import pipeline as _pipeline

# В единой модели все типы маршрутов загружаются одинаково.
# Имя BASE_ROUTE_TYPES сохраняем только как compatibility alias для внешнего кода.
_pipeline.BASE_ROUTE_TYPES = frozenset(RouteType)


def _no_pipeline_bbox(*_args: object, **_kwargs: object) -> None:
    """Отключает устаревшую стадию расчёта bbox в pipeline."""
    return None


# bbox больше не является частью общего pipeline-state.
# Bbox-зависимые функции (Overture/POI/generation/heatmap) должны быть
# переведены на собственные области интереса в отдельном рефакторинге.
_pipeline.compute_pipeline_bbox = _no_pipeline_bbox

__all__ = ["main"]
