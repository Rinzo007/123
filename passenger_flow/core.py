"""Основной алгоритм распределения пассажиропотока.

Оркестрация шагов расчёта: привязка зон к остановкам через KD-tree,
перечисление вариантов поездки (прямые + пересадки), частотное Takt-распределение
поездок и накопление агрегатов, после чего результат собирается
в ``FlowResult`` (см. ``assembly``).

Дополнительные опции (по мотивам Takt, playtakt.app) включены по умолчанию:
- ``ModeChoiceConfig`` — конкуренция «транзит / авто / пешком / eBike / rest»:
  стоимости приводятся к обобщённым минутам, денежные элементы — через VOT;
- ``periods`` — разложение OD-матрицы по периодам суток (out/ret);
- ``headway_min`` — ожидание из интервала движения (≈ интервал/2 либо
  модель Takt ``wait_calc="takt"`` с насыщением 6 + 0.1×headway);
- ``wait_crowding_per_100_min`` — рост ожидания с заполнением маршрута;
- ``msa_max_iterations`` — итеративное MSA-присваивание с разрывом <= 1%;
- ``transfer_wait_min`` — отдельное ожидание на пересадке.
"""

from __future__ import annotations

from collections import defaultdict
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

import numpy as np
from scipy.spatial import cKDTree

if TYPE_CHECKING:
    from .models import RouteLike
    from od.model import Zones
else:
    RouteLike = Any
    Zones = Any
from .algorithm.assign import _assign_od
from .algorithm.kpis import _build_line_kpis
from .algorithm.wait import (
    _build_crowd_state,
    _build_wait_extra,
    _expected_wait_min,
    _reliability_min,
    _run_msa_period,
)
from .algorithm.mode_choice import (
    _takt_car_period_multiplier,
    _takt_no_car_shares,
)
from .base.models import (
    FlowResult,
    LineResult,
    ModeChoiceConfig,
    PassengerFlowError,
    Period,
    PeriodFlow,
    VehicleSpec,
    vehicle_spec_for_route_type,
)
from .base.takt import TAKT_PERIODS, _TAKT_PERIOD_HOURS
from .network.geometry import _find_nearest_stops, haversine_meters
from .network.routes import _build_route_stop_sequence, _build_transfer_edge_index
from .report.assembly import assemble_flow_result

_LOGIT_TEMP = 10.0
_DEFAULT_STOP_TIME_MIN = 2.0
_DEFAULT_HEADWAY_MIN = 10.0
_DEFAULT_MAX_TRANSFERS = 3
_DEFAULT_WAIT_CROWDING_PER_100_MIN = 0.1
_DEFAULT_MSA_MAX_ITERATIONS = 20
_DEFAULT_MSA_GAP = 0.01

def _takt_car_period_multipliers(
    od_rows: np.ndarray,
    od_cols: np.ndarray,
    od_vals: np.ndarray,
    period_sources: Sequence[Period | None],
) -> tuple[float, ...]:
    """Строит yt для каждого периода из того же спроса, который назначает flow.

    Для верхнего треугольника используется out, для нижнего — ret; затем
    периодный спрос делится на часы периода и сравнивается со средним
    спросом в час по всем периодам.
    """
    if len(period_sources) == 1 and period_sources[0] is None:
        return (1.0,)
    upper = 0.0
    lower = 0.0
    for row, col, value in zip(od_rows, od_cols, od_vals):
        trips = float(value)
        if trips <= 0.0 or int(row) == int(col):
            continue
        if int(row) < int(col):
            upper += trips
        else:
            lower += trips
    period_demand = []
    total_hours = 0.0
    total_demand = 0.0
    for period in period_sources:
        if period is None:
            hours = 24.0
            demand = upper + lower
        else:
            hours = float(_TAKT_PERIOD_HOURS.get(period.key, 24.0))
            demand = upper * float(period.out) + lower * float(period.ret)
        hours = max(hours, 1e-12)
        period_demand.append((demand, hours))
        total_demand += demand
        total_hours += hours
    average = total_demand / max(total_hours, 1e-12)
    return tuple(
        _takt_car_period_multiplier(demand / hours, average)
        for demand, hours in period_demand
    )


class Reporter(Protocol):
    """Минимальный интерфейс логгера хода расчёта."""

    def line(self, text: str) -> None: ...


@dataclass(frozen=True, slots=True)
class PreparedPassengerFlow:
    """Подготовленный статический контекст для повторных расчётов OD.

    Включает последовательности маршрутов, KD-tree остановок, привязку зон
    и reusable transfer spatial index для стандартного радиуса пересадки.
    """
    zones: Zones
    stop_search_radius_m: float
    route_sequences: tuple[dict[str, Any], ...]
    zone_nearest: dict[int, list[tuple[int, int, int, float]]]
    transfer_index: Mapping[tuple[int, int], tuple[tuple[int, dict[str, Any], dict[str, Any]], ...]] | None = None


def prepare_passenger_flow(
    routes: list[RouteLike],
    zones: Zones,
    *,
    stop_search_radius_m: float = 1500.0,
) -> PreparedPassengerFlow:
    """Предвычисляет неизменяемую часть пассажиропотока для повторных OD-запусков."""
    if stop_search_radius_m <= 0.0:
        raise PassengerFlowError("stop_search_radius_m должен быть положительным")
    route_sequences = _build_route_stop_sequence(routes)
    if route_sequences:
        stop_coords, stop_tree, flat_map = _build_stop_index(route_sequences)
        zone_nearest = _bind_zones_to_stops(
            zones, stop_coords, stop_tree, flat_map, stop_search_radius_m
        )
    else:
        zone_nearest = {zi: [] for zi in range(len(zones))}
    return PreparedPassengerFlow(
        zones=zones,
        stop_search_radius_m=float(stop_search_radius_m),
        route_sequences=tuple(route_sequences),
        zone_nearest=zone_nearest,
        transfer_index=_build_transfer_edge_index(route_sequences, 800.0),
    )

# ===== Валидация входных параметров =====


def _finite_number(name: str, value: float, *, nonnegative: bool = False, positive: bool = False) -> None:
    value = float(value)
    if not math.isfinite(value):
        raise PassengerFlowError(f"{name} должен быть конечным числом")
    if positive and value <= 0.0:
        raise PassengerFlowError(f"{name} должен быть положительным")
    if nonnegative and value < 0.0:
        raise PassengerFlowError(f"{name} не может быть отрицательным")

def _validate_od_matrix(matrix: np.ndarray, n_zones: int) -> None:
    if matrix.shape != (n_zones, n_zones):
        raise PassengerFlowError(
            f"Матрица OD {matrix.shape} не совпадает с числом зон {n_zones}"
        )
    if not np.isfinite(matrix).all():
        raise PassengerFlowError("Матрица OD содержит NaN или Inf")
    if np.any(matrix < 0.0):
        raise PassengerFlowError("Матрица OD содержит отрицательные поездки")


def _validate_transfer_args(
    max_transfers: int, transfer_radius_m: float
) -> None:
    if not isinstance(max_transfers, int):
        raise PassengerFlowError("max_transfers должен быть целым числом")
    if max_transfers < 0:
        raise PassengerFlowError("max_transfers не может быть отрицательным")
    if max_transfers > 3:
        raise PassengerFlowError(
            "Поддерживается не более трёх пересадок (до 4 ножек)"
        )
    _finite_number("transfer_radius_m", transfer_radius_m, positive=True)


def _validate_headway_args(
    headway_min: float | None,
    headway_by_route: Mapping[int, float] | None,
) -> None:
    if headway_min is not None:
        _finite_number("headway_min", headway_min, positive=True)
    if headway_by_route is not None:
        for rid, value in headway_by_route.items():
            _finite_number(f"headway_by_route[{rid}]", value, positive=True)


def _validate_capex_args(capex_factor: float, capex_amort_years: float) -> None:
    _finite_number("capex_factor", capex_factor, nonnegative=True)
    _finite_number("capex_amort_years", capex_amort_years, positive=True)


def _validate_waiting_args(
    wait_crowding_per_100_min: float,
    transfer_wait_min: float | None,
    transfer_penalty_calc: str,
    wait_calc: str,
) -> None:
    _finite_number("wait_crowding_per_100_min", wait_crowding_per_100_min, nonnegative=True)
    if transfer_wait_min is not None:
        _finite_number("transfer_wait_min", transfer_wait_min, nonnegative=True)
    if transfer_penalty_calc not in ("fixed", "takt"):
        raise PassengerFlowError(
            "transfer_penalty_calc должен быть 'fixed' или 'takt'"
        )
    if wait_calc not in ("linear", "takt"):
        raise PassengerFlowError("wait_calc должен быть 'linear' или 'takt'")


def _validate_reliability_args(
    include_reliability: bool, headway_min: float | None
) -> None:
    if include_reliability and headway_min is None:
        raise PassengerFlowError(
            "include_reliability требует заданного headway_min"
        )


def _validate_msa_args(
    msa_max_iterations: int | None,
    wait_crowding_per_100_min: float,
    msa_gap: float,
) -> None:
    if msa_max_iterations is not None:
        if not isinstance(msa_max_iterations, int) or msa_max_iterations < 1:
            raise PassengerFlowError("msa_max_iterations должен быть положительным целым числом")
    if msa_max_iterations is not None and wait_crowding_per_100_min <= 0.0:
        raise PassengerFlowError(
            "MSA-присваивание требует wait_crowding_per_100_min > 0"
        )
    if msa_max_iterations is not None:
        _finite_number("msa_gap", msa_gap)
        if not 0.0 < float(msa_gap) <= 1.0:
            raise PassengerFlowError("msa_gap должен быть в диапазоне (0, 1]")


def _validate_periods(periods: Sequence[Period]) -> None:
    for p in periods:
        _finite_number(f"Период «{p.key}».out", p.out, nonnegative=True)
        _finite_number(f"Период «{p.key}».ret", p.ret, nonnegative=True)


def _validate_mode_choice(mode_choice: ModeChoiceConfig) -> None:
    if not 0.0 <= mode_choice.car_no_car_share <= 1.0:
        raise PassengerFlowError(
            "car_no_car_share должен быть в диапазоне [0, 1]"
        )
    _finite_number("car_no_car_share", mode_choice.car_no_car_share)
    _finite_number("car_no_car_factor", mode_choice.car_no_car_factor, nonnegative=True)
    _finite_number("car_speed_kmh", mode_choice.car_speed_kmh, positive=True)
    _finite_number("walk_speed_mps", mode_choice.walk_speed_mps, positive=True)
    _finite_number("vot_per_eur_s", mode_choice.vot_per_eur_s, positive=True)
    if not 0.0 <= mode_choice.car_no_car_share <= 1.0:
        raise PassengerFlowError(
            "car_no_car_share должен быть в диапазоне [0, 1]"
        )
    if not 0.0 <= mode_choice.two_wheel_share <= 1.0:
        raise PassengerFlowError(
            "two_wheel_share должен быть в диапазоне [0, 1]"
        )
    _finite_number("two_wheel_speed_mps", mode_choice.two_wheel_speed_mps, positive=True)
    _finite_number("two_wheel_reach_m", mode_choice.two_wheel_reach_m, nonnegative=True)
    if not 0.0 <= mode_choice.two_wheel_share <= 1.0:
        raise PassengerFlowError(
            "two_wheel_share должен быть в диапазоне [0, 1]"
        )
    for name, value in (
        ("car_parking_min", mode_choice.car_parking_min),
        ("car_cost_per_km_eur", mode_choice.car_cost_per_km_eur),
        ("car_parking_eur", mode_choice.car_parking_eur),
        ("car_circuity", mode_choice.car_circuity),
        ("walk_circuity", mode_choice.walk_circuity),
        ("fare_base_eur", mode_choice.fare_base_eur),
        ("fare_per_km_eur", mode_choice.fare_per_km_eur),
        ("fare_cap_eur", mode_choice.fare_cap_eur),
        ("two_wheel_per_km_eur", mode_choice.two_wheel_per_km_eur),
        ("two_wheel_fixed_s", mode_choice.two_wheel_fixed_s),
        ("two_wheel_circuity", mode_choice.two_wheel_circuity),
        ("rider_bias_s", mode_choice.rider_bias_s),
        ("rest_base_speed_kmh", mode_choice.rest_base_speed_kmh),
        ("rest_cont_speed_kmh", mode_choice.rest_cont_speed_kmh),
        ("rest_access_s", mode_choice.rest_access_s),
        ("rest_wait_s", mode_choice.rest_wait_s),
        ("rest_circuity", mode_choice.rest_circuity),
    ):
        _finite_number(name, value, nonnegative=True)


def _validate_sparse_od(od_sparse: Any, n_zones: int) -> None:
    """Проверяет duck-typed sparse OD до его использования в assignment."""
    if od_sparse is None:
        return
    shape = getattr(od_sparse, "shape", None)
    if shape != (n_zones, n_zones):
        raise PassengerFlowError(
            f"od_sparse имеет размер {shape}, ожидается {(n_zones, n_zones)}"
        )
    data = getattr(od_sparse, "data", None)
    if data is None:
        raise PassengerFlowError("od_sparse должен иметь поле data")
    values = np.asarray(data, dtype=np.float64)
    if not np.isfinite(values).all() or np.any(values < 0.0):
        raise PassengerFlowError(
            "od_sparse.data должен содержать конечные неотрицательные значения"
        )

def _validate_base_time(
    base_time_s: np.ndarray | None,
    n_zones: int,
    n_periods: int,
    n_pairs: int,
) -> None:
    """Проверяет baseT: NxN, [period,N,N] или Takt [period,pair]."""
    if base_time_s is None:
        return
    if base_time_s.ndim == 2:
        is_pair_profile = (
            base_time_s.shape[0] >= n_periods
            and base_time_s.shape[0] != base_time_s.shape[1]
        )
        if is_pair_profile:
            if base_time_s.shape[1] != n_pairs:
                raise PassengerFlowError(
                    "base_time_s имеет неверный размер [period,pair]"
                )
        elif base_time_s.shape != (n_zones, n_zones):
            raise PassengerFlowError("base_time_s имеет неверный размер NxN")
    elif base_time_s.ndim == 3:
        if base_time_s.shape[1:] != (n_zones, n_zones):
            raise PassengerFlowError("base_time_s имеет неверные размеры [period,N,N]")
        if base_time_s.shape[0] < n_periods:
            raise PassengerFlowError("base_time_s содержит меньше периодов, чем periods")
    else:
        raise PassengerFlowError("base_time_s должен быть NxN, [period,N,N] или [period,pair]")
    if np.isinf(base_time_s).any() or np.any(
        np.isfinite(base_time_s) & (base_time_s < 0.0)
    ):
        raise PassengerFlowError(
            "base_time_s должен содержать неотрицательные значения или NaN"
        )

def _validate_flow_inputs(
    matrix: np.ndarray,
    n_zones: int,
    *,
    max_transfers: int,
    transfer_radius_m: float,
    transfer_wait_min: float | None,
    transfer_penalty_calc: str,
    headway_min: float | None,
    headway_by_route: Mapping[int, float] | None,
    wait_calc: str,
    include_reliability: bool,
    capex_factor: float,
    capex_amort_years: float,
    wait_crowding_per_100_min: float,
    msa_max_iterations: int | None,
    msa_gap: float,
    periods: Sequence[Period],
    mode_choice: ModeChoiceConfig,
    base_time_s: np.ndarray | None,
    od_sparse: Any,
    stop_search_radius_m: float,
    stop_time_min: float,
    wait_time_min: float,
    walk_to_stop_min: float,
    transfer_penalty_min: float,
    logit_temp: float,
) -> None:
    """Полная валидация входов — композиция групп проверок.

    Порядок вызовов совпадает с порядком исходных if/raise, чтобы при
    нескольких нарушениях сообщение об ошибке не менялось.
    """
    _validate_od_matrix(matrix, n_zones)
    _finite_number("stop_search_radius_m", stop_search_radius_m, positive=True)
    _finite_number("stop_time_min", stop_time_min, nonnegative=True)
    _finite_number("wait_time_min", wait_time_min, nonnegative=True)
    _finite_number("walk_to_stop_min", walk_to_stop_min, nonnegative=True)
    _finite_number("transfer_penalty_min", transfer_penalty_min, nonnegative=True)
    _finite_number("logit_temp", logit_temp, positive=True)
    _validate_base_time(\n        base_time_s,\n        n_zones,\n        len(periods) if periods else 1,\n        int(np.count_nonzero(matrix > 0.0)),\n    )
    _validate_sparse_od(od_sparse, n_zones)
    _validate_transfer_args(max_transfers, transfer_radius_m)
    _validate_headway_args(headway_min, headway_by_route)
    _validate_capex_args(capex_factor, capex_amort_years)
    _validate_waiting_args(
        wait_crowding_per_100_min,
        transfer_wait_min,
        transfer_penalty_calc,
        wait_calc,
    )
    _validate_reliability_args(include_reliability, headway_min)
    _validate_msa_args(msa_max_iterations, wait_crowding_per_100_min, msa_gap)
    _validate_periods(periods)
    _validate_mode_choice(mode_choice)


# ===== Индекс остановок и привязка зон =====


def _validate_route_sequences(route_sequences: Sequence[Mapping[str, Any]]) -> None:
    """Проверяет подготовленный route graph до запуска OD assignment."""
    for seq_i, seq in enumerate(route_sequences):
        stops = seq.get("stops")
        if not isinstance(stops, list) or not stops:
            raise PassengerFlowError(f"route sequence {seq_i} не содержит остановок")
        for stop_i, stop in enumerate(stops):
            try:
                lat = float(stop["lat"])
                lon = float(stop["lon"])
            except (KeyError, TypeError, ValueError) as exc:
                raise PassengerFlowError(
                    f"route sequence {seq_i}: остановка {stop_i} имеет некорректные координаты"
                ) from exc
            if not math.isfinite(lat) or not math.isfinite(lon) or abs(lat) > 90.0 or abs(lon) > 180.0:
                raise PassengerFlowError(
                    f"route sequence {seq_i}: остановка {stop_i} имеет некорректные координаты"
                )
        cum = seq.get("cum_t_s")
        if cum is not None:
            try:
                values = [float(v) for v in cum]
            except (TypeError, ValueError) as exc:
                raise PassengerFlowError(f"route sequence {seq_i}: cum_t_s имеет некорректный формат") from exc
            if len(values) != len(stops) or not all(math.isfinite(v) for v in values):
                raise PassengerFlowError(f"route sequence {seq_i}: cum_t_s имеет некорректное значение")
            if any(values[i + 1] < values[i] for i in range(len(values) - 1)):
                raise PassengerFlowError(f"route sequence {seq_i}: cum_t_s должен быть неубывающим")
        cycle = seq.get("cycle_run_s")
        if cycle is not None:
            cycle_value = float(cycle)
            if not math.isfinite(cycle_value) or cycle_value < 0.0:
                raise PassengerFlowError(
                    f"route sequence {seq_i}: cycle_run_s должен быть конечным и неотрицательным"
                )

def _build_stop_index(
    route_sequences: list[dict[str, Any]],
) -> tuple[np.ndarray, cKDTree, list[tuple[int, int, int]]]:
    """Строит KD-tree по координатам остановок всех маршрутов.

    ``flat_map`` переводит плоский индекс остановки в
    ``(seq_index, stop_index, position)``.
    """
    coords: list[tuple[float, float]] = []
    flat_map: list[tuple[int, int, int]] = []
    for seq_index, seq in enumerate(route_sequences):
        for stop_index, stop in enumerate(seq["stops"]):
            coords.append((stop["lat"], stop["lon"]))
            flat_map.append((seq_index, stop_index, stop["position"]))
    coords_arr = np.array(coords, dtype=np.float64)
    return coords_arr, cKDTree(coords_arr), flat_map


def _bind_zones_to_stops(
    zones: Zones,
    stop_coords: np.ndarray,
    stop_tree: cKDTree,
    flat_map: list[tuple[int, int, int]],
    stop_search_radius_m: float,
) -> dict[int, list[tuple[int, int, int, float]]]:
    """Привязывает зоны к ближайшим остановкам в радиусе поиска.

    Каждый кандидат несёт расстояние до центроида зоны (м): линия из
    маршрута обслуживает зону, только если этот запас <= её ``access_m``
    (500 bus / 600 tram / 800 metro / 1500 rail, как в движке Takt).
    """
    zone_nearest: dict[int, list[tuple[int, int, int, float]]] = {}
    for zi in range(len(zones)):
        centroid_lon, centroid_lat = zones.xy[zi]
        nearest_idx = _find_nearest_stops(
            centroid_lat,
            centroid_lon,
            stop_coords,
            stop_tree,
            radius_m=stop_search_radius_m,
        )
        entries: list[tuple[int, int, int, float]] = []
        for fi in nearest_idx:
            seqi, stopi, posi = flat_map[fi]
            dist_m = haversine_meters(
                centroid_lat,
                centroid_lon,
                float(stop_coords[fi][0]),
                float(stop_coords[fi][1]),
            )
            entries.append((seqi, stopi, posi, float(dist_m)))
        zone_nearest[zi] = entries
    return zone_nearest


# ===== OD-пары и тайминги маршрутов =====


def _load_od_pairs(
    od_sparse: Any, matrix: np.ndarray, line: Any
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Возвращает (rows, cols, vals) ненулевых OD-пар."""
    if od_sparse is not None:
        od_rows, od_cols = od_sparse.nonzero()
        line(f"  OD-пар с поездками: {len(od_rows):,} (sparse)")
        return od_rows, od_cols, od_sparse.data
    od_rows, od_cols = np.nonzero(matrix > 0)
    line(f"  OD-пар с поездками: {len(od_rows):,}")
    return od_rows, od_cols, matrix[od_rows, od_cols]


def _period_seq_headways(
    route_sequences: Sequence[Mapping[str, Any]],
    headway_min: float | None,
    headway_by_route: Mapping[int, float] | None,
    period_index: int,
) -> dict[int, float] | None:
    """Возвращает headway каждой sequence для конкретного Takt-периода."""
    if headway_min is None and headway_by_route is None and not route_sequences:
        return None
    result: dict[int, float] = {}
    for seq_idx, seq in enumerate(route_sequences):
        raw = seq.get("headways")
        hv: float | None = None
        if isinstance(raw, (list, tuple)) and period_index < len(raw):
            try:
                candidate = float(raw[period_index])
                if candidate > 0.0 and math.isfinite(candidate):
                    hv = candidate
            except (TypeError, ValueError):
                hv = None
        rid = int(seq["route_id"])
        if hv is None and headway_by_route is not None and rid in headway_by_route:
            hv = float(headway_by_route[rid])
        if hv is None and headway_min is not None:
            hv = float(headway_min)
        if hv is not None and hv > 0.0:
            result[seq_idx] = hv
    return result or None

def _prepare_seq_timing(
    route_sequences: list[dict[str, Any]],
    headway_min: float | None,
    headway_by_route: Mapping[int, float] | None,
    vehicle_specs: Mapping[str, VehicleSpec] | None,
    include_reliability: bool,
) -> tuple[
    dict[int, float] | None,
    dict[int, float] | None,
    dict[int, float] | None,
]:
    """Готовит per-seq headway/jitter для координации пересадок (jo/hs).

    Возвращает ``(seq_headway_min, seq_jitter_s, reliability_extra)``;
    первые два — None, если ``headway_min`` не задан, третий — None без
    ``include_reliability``.
    """
    if headway_min is None:
        return None, None, None
    seq_headway_min: dict[int, float] = {}
    seq_jitter_s: dict[int, float] = {}
    reliability_extra: dict[int, float] | None = None
    for seq_idx, seq in enumerate(route_sequences):
        key = str(seq.get("route_type_key") or "").lower()
        spec = vehicle_specs.get(key) if vehicle_specs else None
        if spec is None:
            spec = vehicle_spec_for_route_type(key)
        rid = seq["route_id"]
        hv = (
            headway_by_route.get(rid, headway_min)
            if headway_by_route is not None
            else headway_min
        )
        seq_headway_min[seq_idx] = float(hv)
        seq_jitter_s[seq_idx] = spec.jitter_s
        if include_reliability:
            if reliability_extra is None:
                reliability_extra = {}
            reliability_extra[seq_idx] = _reliability_min(
                spec.jitter_s, float(hv)
            )
    return seq_headway_min, seq_jitter_s, reliability_extra


# ===== Слияние агрегатов =====


def _empty_stop_entry() -> dict[str, Any]:
    """Дефолтная запись остановки для общего ``stop_totals``."""
    return {
        "stop_id": None,
        "name": "",
        "boardings": 0.0,
        "alightings": 0.0,
        "lat": 0.0,
        "lon": 0.0,
    }


def _merge_stop_entry(target: dict[str, Any], data: dict[str, Any]) -> None:
    """Сливает одну запись остановки: счётчики суммируются, метаданные —
    из первого непустого источника."""
    target["boardings"] += data["boardings"]
    target["alightings"] += data["alightings"]
    if not target["name"]:
        target["name"] = data["name"]
    if target["stop_id"] is None:
        target["stop_id"] = data["stop_id"]
    if not target["lat"] and not target["lon"]:
        target["lat"] = data["lat"]
        target["lon"] = data["lon"]


def _merge_pass_aggregates(
    accum: dict[str, Any], pass_agg: dict[str, Any]
) -> None:
    """Сливает агрегаты одного прохода в общие аккумуляторы (in-place)."""
    for rid, val in pass_agg["route_totals"].items():
        accum["route_totals"][rid] += val
    for key, val in pass_agg["dir_totals"].items():
        accum["dir_totals"][key] += val
    for key, data in pass_agg["stop_totals"].items():
        _merge_stop_entry(accum["stop_totals"][key], data)
    for key, data in pass_agg["route_stop_totals"].items():
        target = accum["route_stop_totals"][key]
        for sname, sval in data.items():
            target[sname] = target.get(sname, 0.0) + sval
    for key, val in pass_agg.get("seg_totals", {}).items():
        accum["seg_totals"][key] += val
    for key, val in pass_agg.get("seg_forward_totals", {}).items():
        accum["seg_forward_totals"][key] += val
    for key, val in pass_agg.get("seg_reverse_totals", {}).items():
        accum["seg_reverse_totals"][key] += val
    for key, val in pass_agg.get("seq_stop_totals", {}).items():
        accum["seq_stop_totals"][key] += val
    accum["assigned_trips"] += pass_agg["assigned_trips"]
    accum["car_trips"] += pass_agg["car_trips"]
    accum["walk_trips"] += pass_agg["walk_trips"]
    accum["two_wheel_trips"] += pass_agg["two_wheel_trips"]
    accum["rest_trips"] += pass_agg.get("rest_trips", 0.0)
    accum["fare_revenue"] += pass_agg["fare_revenue"]


def _empty_accumulator() -> dict[str, Any]:
    """Общие аккумуляторы, в которые сливаются все периоды."""
    return {
        "assigned_trips": 0.0,
        "car_trips": 0.0,
        "walk_trips": 0.0,
        "two_wheel_trips": 0.0,
        "rest_trips": 0.0,
        "fare_revenue": 0.0,
        "route_totals": defaultdict(float),
        "dir_totals": defaultdict(float),
        "stop_totals": defaultdict(_empty_stop_entry),
        "route_stop_totals": defaultdict(dict),
        "seg_totals": defaultdict(float),
        "seg_forward_totals": defaultdict(float),
        "seg_reverse_totals": defaultdict(float),
        "seq_stop_totals": defaultdict(float),
    }


# ===== Контекст прохода (общие аргументы для _assign_od/_run_msa_period) =====


@dataclass(frozen=True)
class _AssignContext:
    """Общие аргументы проходов ``_assign_od``/``_run_msa_period``.

    Позволяет вызывать проход с минимумом per-call параметров
    (``out_factor``/``ret_factor``/``wait_extra``), не дублируя 18 kwargs.
    """

    od_rows: np.ndarray
    od_cols: np.ndarray
    od_vals: np.ndarray
    zone_nearest: Mapping[int, list[tuple[int, int, int, float]]]
    route_sequences: list[dict[str, Any]]
    stop_time_min: float
    wait_base: float
    walk_to_stop_min: float
    transfer_penalty_min: float
    transfer_wait_min: float | None
    transfer_radius_m: float
    max_transfers: int
    transfer_penalty_calc: str
    logit_temp: float
    mode_choice: ModeChoiceConfig | None
    zones: Zones
    no_car_shares: np.ndarray | None
    base_time_s: np.ndarray | None
    seq_headway_min: Mapping[int, float] | None
    seq_jitter_s: Mapping[int, float] | None
    seq_headway_periods: tuple[Mapping[int, float] | None, ...]
    wait_calc: str
    vehicle_specs: Mapping[str, VehicleSpec] | None
    car_period_multipliers: tuple[float, ...]
    transfer_index: Mapping[tuple[int, int], tuple[tuple[int, dict[str, Any], dict[str, Any]], ...]]

    def _common_kwargs(self, period_index: int | None = None) -> dict[str, Any]:
        headway = (
            self.seq_headway_min
            if period_index is None
            else self.seq_headway_periods[period_index]
            if period_index < len(self.seq_headway_periods)
            else self.seq_headway_min
        )
        return {
            "stop_time_min": self.stop_time_min,
            "wait_time_min": self.wait_base,
            "walk_to_stop_min": self.walk_to_stop_min,
            "transfer_penalty_min": self.transfer_penalty_min,
            "transfer_wait_min": self.transfer_wait_min,
            "transfer_radius_m": self.transfer_radius_m,
            "max_transfers": self.max_transfers,
            "transfer_penalty_calc": self.transfer_penalty_calc,
            "logit_temp": self.logit_temp,
            "mode": self.mode_choice,
            "zones": self.zones,
            "no_car_shares": self.no_car_shares,
            "base_time_s": self.base_time_s,
            "seq_headway_min": headway,
            "seq_jitter_s": self.seq_jitter_s,
            "wait_calc": self.wait_calc,
            "transfer_index": self.transfer_index,
        }

    def assign(
        self,
        *,
        out_factor: float,
        ret_factor: float,
        wait_extra: Mapping[int, float] | None,
        period_index: int,
        crowd_state: Mapping[str, Mapping[tuple[int, int], float]] | None = None,
    ) -> dict[str, Any]:
        return _assign_od(
            self.od_rows,
            self.od_cols,
            self.od_vals,
            self.zone_nearest,
            self.route_sequences,
            out_factor=out_factor,
            ret_factor=ret_factor,
            wait_extra=wait_extra,
            period_index=period_index,
            crowd_state=crowd_state,
            car_period_multiplier=(
                self.car_period_multipliers[period_index]
                if period_index < len(self.car_period_multipliers)
                else 1.0
            ),
            transfer_index=self.transfer_index,
            **{k: v for k, v in self._common_kwargs(period_index).items() if k != "transfer_index"},
        )

    def msa(
        self,
        *,
        out_factor: float,
        ret_factor: float,
        wait_crowding_per_100_min: float,
        reliability_extra: Mapping[int, float] | None,
        max_iterations: int,
        gap_tol: float,
        period_index: int,
        period_hours: float,
    ) -> tuple[dict[str, Any], int, float]:
        return _run_msa_period(
            self.od_rows,
            self.od_cols,
            self.od_vals,
            self.zone_nearest,
            self.route_sequences,
            out_factor=out_factor,
            ret_factor=ret_factor,
            wait_crowding_per_100_min=wait_crowding_per_100_min,
            reliability_extra=reliability_extra,
            max_iterations=max_iterations,
            gap_tol=gap_tol,
            period_index=period_index,
            period_hours=period_hours,
            car_period_multiplier=(
                self.car_period_multipliers[period_index]
                if period_index < len(self.car_period_multipliers)
                else 1.0
            ),
            **self._common_kwargs(period_index),
        )


# ===== Один период =====


def _run_period(
    ctx: _AssignContext,
    period: Period | None,
    *,
    period_index: int,
    period_hours: float,
    wait_crowding_per_100_min: float,
    reliability_extra: Mapping[int, float] | None,
    msa_max_iterations: int | None,
    msa_gap: float,
    line: Any,
) -> dict[str, Any]:
    """Один проход (или MSA-серия) для одного периода.

    Режим выбирается автоматически: MSA при заданных
    ``msa_max_iterations`` + crowding; иначе crowding (двухпроходный)
    или обычный (один проход).
    """
    out_factor = period.out if period is not None else 1.0
    ret_factor = period.ret if period is not None else 1.0

    if wait_crowding_per_100_min > 0 and msa_max_iterations is not None:
        pass_agg, msa_iters, msa_final_gap = ctx.msa(
            out_factor=out_factor,
            ret_factor=ret_factor,
            wait_crowding_per_100_min=wait_crowding_per_100_min,
            reliability_extra=reliability_extra,
            max_iterations=msa_max_iterations,
            gap_tol=msa_gap,
            period_index=period_index,
            period_hours=period_hours,
        )
        line(
            f"  MSA {period.key if period else 'общий'}: "
            f"{msa_iters} итераций, разрыв {msa_final_gap*100:.2f}%"
        )
        return pass_agg

    if wait_crowding_per_100_min > 0:
        pass_one = ctx.assign(
            out_factor=out_factor,
            ret_factor=ret_factor,
            wait_extra=reliability_extra,
            period_index=period_index,
        )
        crowd_state = _build_crowd_state(
            ctx.route_sequences,
            pass_one.get("seg_forward_totals", {}),
            pass_one.get("seg_reverse_totals", {}),
            pass_one.get("seq_stop_totals", {}),
            ctx.seq_headway_min,
            ctx.vehicle_specs,
            period_hours,
        )
        return ctx.assign(
            out_factor=out_factor,
            ret_factor=ret_factor,
            wait_extra=reliability_extra,
            period_index=period_index,
            crowd_state=crowd_state,
        )

    return ctx.assign(
        out_factor=out_factor,
        ret_factor=ret_factor,
        wait_extra=reliability_extra,
        period_index=period_index,
    )


# ===== Главная точка входа =====


def run_passenger_flow(
    routes: list[RouteLike],
    od_matrix: np.ndarray,
    zones: Zones,
    *,
    population: np.ndarray | None = None,
    base_time_s: np.ndarray | None = None,
    od_sparse: Any = None,
    stop_time_min: float = _DEFAULT_STOP_TIME_MIN,
    logit_temp: float = _LOGIT_TEMP,
    stop_search_radius_m: float = 1500.0,
    wait_time_min: float = 0.0,
    walk_to_stop_min: float = 0.0,
    transfer_penalty_min: float = 10.0,
    transfer_radius_m: float = 800.0,
    max_transfers: int = _DEFAULT_MAX_TRANSFERS,
    transfer_penalty_calc: str = "takt",
    headway_min: float | None = _DEFAULT_HEADWAY_MIN,
    wait_crowding_per_100_min: float = _DEFAULT_WAIT_CROWDING_PER_100_MIN,
    transfer_wait_min: float | None = None,
    periods: Sequence[Period] = TAKT_PERIODS,
    mode_choice: ModeChoiceConfig | None = None,
    vehicle_specs: Mapping[str, VehicleSpec] | None = None,
    headway_by_route: Mapping[int, float] | None = None,
    capex_factor: float = 1.0,
    capex_amort_years: float = 30.0,
    reporter: Any = None,
    wait_calc: str = "takt",
    include_reliability: bool = True,
    msa_max_iterations: int | None = _DEFAULT_MSA_MAX_ITERATIONS,
    msa_gap: float = _DEFAULT_MSA_GAP,
    prepared: PreparedPassengerFlow | None = None,
) -> FlowResult:
    """Выполняет расчёт пассажиропотока на маршрутах и остановках.

    Parameters
    ----------
    routes : list
        Маршруты с ``.directions[].stops[]`` (структурно соответствует ``RouteLike``).
    od_matrix : np.ndarray
        Матрица OD (поездки между зонами). Должна быть квадратной
        ``(len(zones) x len(zones))``, конечной и неотрицательной.
    zones : Zones
        Зоны транспортной сетки.
    population : optional
        Население по зонам. При передаче движок применяет плотностную поправку
        Takt к доле домохозяйств без автомобиля для каждого origin.
    base_time_s : optional
        Базовое время альтернативы rest/baseT в секундах: матрица NxN
        либо массив [период, NxN].
    od_sparse : optional
        CSR-представление OD-матрицы (scipy.sparse.csr_matrix) для обхода
        только ненулевых пар; при None ненулевые пары ищутся по ``od_matrix``.
    stop_time_min : float
        Среднее время проезда между соседними остановками (мин).
    logit_temp : float
        Унаследованный параметр API; основная модель выбора режима использует иерархию Takt.
    stop_search_radius_m : float
        Окно поиска остановок у зоны (м); итоговый доступ к остановке
        ограничен радиусом типа маршрута (``access_m``: bus 500 / tram 600 /
        metro 800 / rail 1500), как в движке Takt.
    wait_time_min : float
        Среднее время ожидания перед каждой посадкой (мин).
    walk_to_stop_min : float
        Среднее время подхода к остановке (мин), добавляется к каждой поездке.
    transfer_penalty_min : float
        Штраф за каждую пересадку (мин); используется при
        ``transfer_penalty_calc="fixed"``.
    transfer_penalty_calc : str
        Формула штрафа за пересадку: ``fixed`` (фикс ``transfer_penalty_min``)
        или ``takt`` (``6.75 + ходьба/60`` мин по фактическому расстоянию между
        остановками, потолок 800 м — модель Takt).
    transfer_radius_m : float
        Радиус совмещения остановок при пересадке (м, по умолчанию 800 =
        ``An``/``Za`` движка Takt): остановка маршрута A заменяется на остановку
        маршрута B с тем же id или координатами в радиусе.
    max_transfers : int
        Максимальное число пересадок; по умолчанию 3 (до 4 ножек) с ограничением числа альтернатив.
    headway_min : float | None
        Интервал движения (мин), по умолчанию 10.0. При заданном интервале ожидание
        выбирается по ``wait_calc``; режим ``takt`` использует функцию Po.
    wait_crowding_per_100_min : float
        Параметр feedback перегрузки; по умолчанию 0.1 мин на 100 условных пассажиров.
        При значении > 0 рассчитывается сегментная и остановочная crowding-коррекция Takt.
    transfer_wait_min : float | None
        Ожидание на пересадочной посадке (мин); при None равно ``wait_time_min``.
    periods : Sequence[Period]
        Периоды суток для разложения OD по направлениям (out/ret). По умолчанию
        используются пять периодов Takt; пустая последовательность явно отключает разбиение.
    mode_choice : ModeChoiceConfig | None
        Конкуренция «транзит / авто / пешком / eBike». При ``None`` используется
        ``ModeChoiceConfig()`` с дефолтами Takt; при переданном ``population``
        доля noCar дополнительно корректируется по плотности origin-зоны.
    wait_calc : str
        Формула ожидания по интервалу: ``linear`` (``max(wait, headway/2)``)
        или ``takt`` (``headway/2`` при headway <= 12 мин, иначе
        ``6 + 0.1 × headway``).
    include_reliability : bool
        Добавлять к ожиданию штраф надёжности расписания; по умолчанию True.
        Используется ``max(20 с, hypot(jitter_s, 0.4 × headway_с))`` и требует headway.
    msa_max_iterations : int | None
        Число итераций MSA; по умолчанию 20. Нагрузка каждой итерации смешивается
        с предыдущими шагом 1/итерация, остановка по разрыву ``msa_gap``.
    msa_gap : float
        Относительный разрыв нагрузок маршрутов для остановки MSA (в Takt 1%).
    reporter : Reporter | None
        Объект Reporter (метод ``line``) для логирования.
    prepared : PreparedPassengerFlow | None
        Предвычисленная сеть/индекс для повторных расчётов с теми же ``zones``
        и радиусом поиска. Позволяет не перестраивать маршруты и KD-tree.

    Returns
    -------
    FlowResult
        Пассажиропоток по маршрутам и остановкам.
    """
    line = reporter.line if reporter is not None else lambda *_a: None
    line("\n[Flow] Расчёт пассажиропотока...")

    n_zones = len(zones)
    matrix = np.asarray(od_matrix, dtype=np.float64)
    population_arr: np.ndarray | None = None
    if population is not None:
        population_arr = np.asarray(population, dtype=np.float64)
        if population_arr.shape != (n_zones,):
            raise PassengerFlowError(
                "population должен иметь длину, равную числу зон"
            )
        if not np.isfinite(population_arr).all() or np.any(population_arr < 0.0):
            raise PassengerFlowError(
                "population должен содержать конечные неотрицательные значения"
            )

    # Выбор режима Takt всегда включён: транзит / авто / пешком / eBike / rest.
    # При отсутствии конфигурации используются дефолты ModeChoiceConfig.
    if mode_choice is None:
        mode_choice = ModeChoiceConfig()

    _validate_flow_inputs(
        matrix,
        n_zones,
        max_transfers=max_transfers,
        transfer_radius_m=transfer_radius_m,
        transfer_wait_min=transfer_wait_min,
        transfer_penalty_calc=transfer_penalty_calc,
        headway_min=headway_min,
        headway_by_route=headway_by_route,
        wait_calc=wait_calc,
        include_reliability=include_reliability,
        capex_factor=capex_factor,
        capex_amort_years=capex_amort_years,
        wait_crowding_per_100_min=wait_crowding_per_100_min,
        msa_max_iterations=msa_max_iterations,
        msa_gap=msa_gap,
        periods=periods,
        mode_choice=mode_choice,
        base_time_s=None if base_time_s is None else np.asarray(base_time_s, dtype=np.float64),
        od_sparse=od_sparse,
        stop_search_radius_m=stop_search_radius_m,
        stop_time_min=stop_time_min,
        wait_time_min=wait_time_min,
        walk_to_stop_min=walk_to_stop_min,
        transfer_penalty_min=transfer_penalty_min,
        logit_temp=logit_temp,
    )

    # 1–2. Подготовка данных маршрутов и индекс остановок.
    if prepared is not None:
        if prepared.zones is not zones:
            raise PassengerFlowError(
                "prepared был создан для другого экземпляра Zones"
            )
        if not np.isclose(
            prepared.stop_search_radius_m,
            float(stop_search_radius_m),
            rtol=0.0,
            atol=1e-9,
        ):
            raise PassengerFlowError(
                "prepared требует тот же stop_search_radius_m"
            )
        route_sequences = list(prepared.route_sequences)
        zone_nearest = prepared.zone_nearest
        prepared_transfer_index = prepared.transfer_index
    else:
        prepared_transfer_index = None
        route_sequences = _build_route_stop_sequence(routes)
        if route_sequences:
            stop_coords, stop_tree, flat_map = _build_stop_index(route_sequences)
            zone_nearest = _bind_zones_to_stops(
                zones, stop_coords, stop_tree, flat_map, stop_search_radius_m
            )
        else:
            zone_nearest = {zi: [] for zi in range(len(zones))}

    _validate_route_sequences(route_sequences)

    if not route_sequences:
        line("  Маршруты с остановками не найдены")
        return FlowResult(
            route_flows=(),
            stop_flows=(),
            total_trips=float(od_matrix.sum()),
            assigned_trips=0.0,
            routes_served=0,
        )

    # 3. OD-пары (sparse или dense)
    total_trips = float(od_matrix.sum())
    od_rows, od_cols, od_vals = _load_od_pairs(od_sparse, matrix, line)

    # 4. Тайминги маршрутов (headway/jitter/reliability)
    wait_base = _expected_wait_min(headway_min, wait_time_min, wait_calc)
    seq_headway_min, seq_jitter_s, reliability_extra = _prepare_seq_timing(
        route_sequences,
        headway_min,
        headway_by_route,
        vehicle_specs,
        include_reliability,
    )

    # 5. Кэш имён и типов маршрутов для финальной агрегации
    route_names: dict[int, str] = {}
    route_types_map: dict[int, str] = {}
    for seq in route_sequences:
        rid = seq["route_id"]
        route_names[rid] = seq["route_name"]
        route_types_map[rid] = seq["route_type"]

    # 6. Контекст и общие аккумуляторы
    period_sources: tuple[Period | None, ...] = tuple(periods) or (None,)
    car_period_multipliers = _takt_car_period_multipliers(
        od_rows, od_cols, od_vals, period_sources
    )
    ctx = _AssignContext(
        od_rows=od_rows,
        od_cols=od_cols,
        od_vals=od_vals,
        zone_nearest=zone_nearest,
        route_sequences=route_sequences,
        stop_time_min=stop_time_min,
        wait_base=wait_base,
        walk_to_stop_min=walk_to_stop_min,
        transfer_penalty_min=transfer_penalty_min,
        transfer_wait_min=transfer_wait_min,
        transfer_radius_m=transfer_radius_m,
        max_transfers=max_transfers,
        transfer_penalty_calc=transfer_penalty_calc,
        logit_temp=logit_temp,
        mode_choice=mode_choice,
        zones=zones,
        no_car_shares=_takt_no_car_shares(mode_choice, population_arr),
        base_time_s=None if base_time_s is None else np.asarray(base_time_s, dtype=np.float64),
        seq_headway_min=seq_headway_min,
        seq_jitter_s=seq_jitter_s,
        seq_headway_periods=tuple(
            _period_seq_headways(route_sequences, headway_min, headway_by_route, pi)
            for pi in range(max(1, len(period_sources)))
        ),
        wait_calc=wait_calc,
        vehicle_specs=vehicle_specs,
        car_period_multipliers=car_period_multipliers,
        transfer_index=(
            prepared_transfer_index
            if prepared is not None and abs(float(transfer_radius_m) - 800.0) <= 1e-9
            else _build_transfer_edge_index(route_sequences, transfer_radius_m)
        ),
    )
    accum = _empty_accumulator()
    period_flows: list[PeriodFlow] = []
    period_seq_stop_totals: list[tuple[Mapping[tuple[int, int], float], float]] = []

    # 7. Проходы по периодам
    for period_index, period in enumerate(period_sources):
        period_hours = float(
            _TAKT_PERIOD_HOURS.get(period.key, 24.0)
            if period is not None
            else 24.0
        )
        pass_agg = _run_period(
            ctx,
            period,
            period_index=period_index,
            period_hours=period_hours,
            wait_crowding_per_100_min=wait_crowding_per_100_min,
            reliability_extra=reliability_extra,
            msa_max_iterations=msa_max_iterations,
            msa_gap=msa_gap,
            line=line,
        )
        _merge_pass_aggregates(accum, pass_agg)
        period_seq_stop_totals.append((dict(pass_agg.get("seq_stop_totals", {})), period_hours))
        if period is not None:
            period_flows.append(
                PeriodFlow(
                    key=period.key,
                    label=period.label,
                    total_trips=pass_agg["period_total"],
                    assigned_trips=pass_agg["assigned_trips"],
                    car_trips=pass_agg["car_trips"],
                    walk_trips=pass_agg["walk_trips"],
                    two_wheel_trips=pass_agg.get("two_wheel_trips", 0.0),
                    rest_trips=pass_agg.get("rest_trips", 0.0),
                )
            )

    # 8. Сборка результатов
    merged_assigned = accum["assigned_trips"]
    merged_car = accum["car_trips"]
    merged_walk = accum["walk_trips"]
    merged_two_wheel = accum["two_wheel_trips"]
    merged_rest = accum["rest_trips"]
    merged_revenue = accum["fare_revenue"]

    intrazonal_trips = float(np.sum(np.diag(od_matrix)))
    line_results: tuple[LineResult, ...] = ()
    if headway_min is not None:
        line_results = tuple(
            _build_line_kpis(
                route_sequences,
                accum["route_totals"],
                headway_min,
                merged_revenue,
                merged_assigned,
                vehicle_specs,
                headway_by_route=headway_by_route,
                capex_factor=capex_factor,
                capex_amort_years=capex_amort_years,
                seg_totals=dict(accum["seg_totals"]) if accum["seg_totals"] else None,
                seg_forward_totals=(
                    dict(accum["seg_forward_totals"])
                    if accum["seg_forward_totals"]
                    else None
                ),
                seg_reverse_totals=(
                    dict(accum["seg_reverse_totals"])
                    if accum["seg_reverse_totals"]
                    else None
                ),
                period_seq_stop_totals=period_seq_stop_totals,
            )
        )
    result = assemble_flow_result(
        route_totals=accum["route_totals"],
        dir_totals=accum["dir_totals"],
        route_types_map=route_types_map,
        route_names=route_names,
        route_stop_totals=accum["route_stop_totals"],
        stop_totals=accum["stop_totals"],
        route_stop_sequences=route_sequences,
        total_trips=total_trips,
        assigned_trips=merged_assigned,
        intrazonal_trips=intrazonal_trips,
        car_trips=merged_car,
        walk_trips=merged_walk,
        two_wheel_trips=merged_two_wheel,
        rest_trips=merged_rest,
        period_flows=tuple(period_flows),
        line_results=line_results,
    )
    interzonal_trips = max(total_trips - intrazonal_trips, 1.0)
    line(
        f"  Маршрутов с пассажиропотоком: {result.routes_served}, "
        f"назначено на транзит: {merged_assigned:,.0f}/{interzonal_trips:,.0f} "
        f"межзональных; авто: {merged_car:,.0f}, пешком: {merged_walk:,.0f}"
        f"{f', eBike: {merged_two_wheel:,.0f}' if merged_two_wheel else ''}"
        f"{f', выручка: {merged_revenue:,.1f} €' if merged_revenue else ''}"
    )
    return result