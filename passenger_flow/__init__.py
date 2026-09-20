"""Расчёт пассажиропотока на маршрутах и остановках.

Алгоритм:
1. Зоны OD-матрицы привязываются к ближайшим остановкам маршрутов.
2. Для каждой OD-пары с ненулевым числом поездок определяются маршруты,
   обслуживающие данные зоны (направление должно содержать остановку
   зоны-источника ДО остановки зоны-назначения). Для маршрута берётся
   один вариант с минимальным временем проезда.
3. При ``max_transfers > 0`` добавляются поездки с одной пересадкой между
   маршрутами (остановка совпадает по id или лежит в радиусе пересадки).
4. Поездки распределяются пропорционально через логит-модель
   ``p(r) = exp(-t(r) / T) / Σ exp(-t(k) / T)``, где ``t(r)`` — время
   проезда (остановки × время остановки + подход к остановке + ожидание +
   штраф за пересадки). Числитель стабилизируется вычитанием максимума.
5. Агрегация: объём по маршрутам, по направлениям, посадки/высадки
   на каждой остановке.

Внутреннее устройство пакета:
- ``core`` — алгоритм распределения поездок и накопление агрегатов;
- ``routes`` — последовательности остановок маршрутов и варианты поездки
  (прямые и с пересадкой);
- ``geometry`` — гаверсинус, привязка зон к остановкам, ключи и пересадки;
- ``assembly`` — сборка итогового ``FlowResult``;
- ``spacing`` — проверка межостановочных расстояний по диапазонам Takt;
- ``models`` — типы результата.
"""

from __future__ import annotations

# Реэкспорт служебных имён: их используют тесты пассажиропотока
# как атрибуты пакета (pf.cKDTree, pf._find_nearest_stops, pf.haversine_meters).
from scipy.spatial import cKDTree as cKDTree

from .base.models import (
    PURPOSE_DEFAULTS,
    VEHICLE_DEFAULTS,
    FlowResult,
    LineResult,
    ModeChoiceConfig,
    PassengerFlowError,
    Period,
    PeriodFlow,
    Purpose,
    VehicleSpec,
    vehicle_spec_for_route_type,
)
from .base.takt import TAKT_FLEET, TAKT_PERIODS
from .base.takt import _takt_hs as _takt_hs
from .base.takt import _takt_jo as _takt_jo
from .base.takt import _takt_logit_prob as _takt_logit_prob
from .base.takt import _takt_logit_shift as _takt_logit_shift
from .core import run_passenger_flow
from .network.geometry import _find_nearest_stops as _find_nearest_stops
from .network.geometry import _takt_transfer_penalty_min as _takt_transfer_penalty_min
from .network.geometry import haversine_meters as haversine_meters
from .network.routes import _build_route_stop_sequence as _build_route_stop_sequence
from .network.spacing import (
    STOP_SPACING_BANDS,
    StopSpacingVerdict,
    check_stop_spacing,
    stop_spacing_band,
    stop_spacing_verdict,
)

__all__ = [
    "PURPOSE_DEFAULTS",
    "STOP_SPACING_BANDS",
    "TAKT_FLEET",
    "TAKT_PERIODS",
    "VEHICLE_DEFAULTS",
    "FlowResult",
    "LineResult",
    "ModeChoiceConfig",
    "PassengerFlowError",
    "Period",
    "PeriodFlow",
    "Purpose",
    "StopSpacingVerdict",
    "VehicleSpec",
    "check_stop_spacing",
    "run_passenger_flow",
    "stop_spacing_band",
    "stop_spacing_verdict",
    "vehicle_spec_for_route_type",
]