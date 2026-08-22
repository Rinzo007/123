"""Публичный фасад XLSX-экспорта.

Реализация находится в ``xlsx_runtime``. Публичный импорт
``wikiroutes.xlsx`` сохраняется без изменений.
"""

from . import xlsx_runtime as _runtime
from .xlsx_runtime import *  # noqa: F401,F403
from .xlsx_dedup import write_dedup_sheet
from .xlsx_poi import write_poi_sheets
from .xlsx_routes import write_routes_sheet

# Runtime build_xlsx обращается к helper-функциям через globals своего модуля.
# Подменяем вынесенные листы, сохраняя публичный API и остальную совместимость.
_runtime._write_routes_sheet = write_routes_sheet
_runtime._write_poi_sheets = write_poi_sheets
_runtime._write_dedup_sheet = write_dedup_sheet
build_xlsx = _runtime.build_xlsx
