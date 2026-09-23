"""Публичный API пакета WikiRoutes."""

from __future__ import annotations

import sys
from pathlib import Path

# Внутренние модули исторически используют как package-relative, так и
# top-level импорты. Добавляем каталог пакета в sys.path, чтобы перенос
# исходников в wikiroutes/ не ломал существующие модули и CLI.
_PACKAGE_DIR = str(Path(__file__).resolve().parent)
if _PACKAGE_DIR not in sys.path:
    sys.path.insert(0, _PACKAGE_DIR)

from .cli import main

__all__ = ["main"]
