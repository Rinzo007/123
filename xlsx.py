"""Публичный фасад XLSX-экспорта.

Реализация находится в ``xlsx_runtime``. Публичный импорт
``wikiroutes.xlsx`` сохраняется без изменений.
"""

from .xlsx_runtime import *  # noqa: F401,F403
from .xlsx_runtime import build_xlsx
