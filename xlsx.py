"""Публичный фасад XLSX-экспорта.

Реализация находится в ``xlsx_runtime``. Публичный импорт
``wikiroutes.xlsx`` сохраняется без изменений.
"""

from . import xlsx_runtime as _runtime
from .xlsx_runtime import *  # noqa: F401,F403
from .xlsx_routes import write_routes_sheet

# Runtime build_xlsx обращается к helper-функциям через globals своего модуля.
# Подменяем только вынесенный лист, сохраняя остальную совместимость.
_runtime._write_routes_sheet = write_routes_sheet
build_xlsx = _runtime.build_xlsx
