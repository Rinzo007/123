"""Публичный фасад CLI.

Основная реализация находится в ``cli_runtime``. Фасад сохраняет прежний
``wikiroutes.cli`` API и оставляет дальнейшее разбиение CLI независимым.
"""

from .cli_runtime import *  # noqa: F401,F403
from .cli_runtime import main, parse_args, run_batch

__all__ = ["BASE_ROUTE_TYPES", "main", "parse_args", "run_batch"]
