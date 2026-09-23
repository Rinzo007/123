"""Публичный фасад CLI.

Основная реализация находится в ``cli_runtime``.
"""

from .cli_runtime import main, parse_args, run_batch

__all__ = ["main", "parse_args", "run_batch"]
