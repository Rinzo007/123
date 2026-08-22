"""Публичный фасад pipeline.

Реализация вынесена в ``pipeline_runtime``. Это сохраняет прежние импорты
``wikiroutes.pipeline`` и создаёт безопасную границу для дальнейшего деления
pipeline на стадии.
"""

from .pipeline_runtime import *  # noqa: F401,F403
