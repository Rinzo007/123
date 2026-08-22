"""Публичный фасад XLSX-экспорта.

Реализация находится в ``xlsx_runtime``. Публичный импорт
``wikiroutes.xlsx`` сохраняется без изменений.
"""

from . import xlsx_runtime as _runtime
from .xlsx_runtime import *  # noqa: F401,F403
from .xlsx_dedup import write_dedup_sheet
from .xlsx_poi import write_poi_sheets
from .xlsx_routes import write_routes_sheet
from .xlsx_simple import (
    write_all_stops_sheet,
    write_errors_sheet,
    write_excluded_sheet,
    write_generated_sheet,
    write_heatmap_sheet,
    write_stop_volumes_sheet,
    write_unique_stops_sheet,
)

# Runtime build_xlsx обращается к helper-функциям через globals своего модуля.
# Подменяем вынесенные листы, сохраняя публичный API и остальную совместимость.
_runtime._write_routes_sheet = write_routes_sheet
_runtime._write_poi_sheets = write_poi_sheets
_runtime._write_dedup_sheet = write_dedup_sheet
_runtime._write_errors_sheet = write_errors_sheet
_runtime._write_excluded_sheet = write_excluded_sheet
_runtime._write_unique_stops_sheet = write_unique_stops_sheet
_runtime._write_stop_volumes_sheet = write_stop_volumes_sheet
_runtime._write_generated_sheet = write_generated_sheet
_runtime._write_heatmap_sheet = write_heatmap_sheet
_runtime._write_all_stops_sheet = write_all_stops_sheet
build_xlsx = _runtime.build_xlsx
