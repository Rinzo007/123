"""Подготовка маршрутов к расчёту: последовательности остановок и варианты поездки.

Здесь собрано построение упорядоченных списков остановок направлений
и ограниченное перечисление вариантов поездки между зонами: прямые пути,
а также пути до 4 ножек с пересадками и максимум тремя альтернативами.
"""

from __future__ import annotations

import heapq
import math
from collections.abc import Iterator, Mapping
from typing import Any, NamedTuple

from scipy.spatial import cKDTree

from ...models import RouteLike
from ...support import type_label
from ..base.models import vehicle_spec_for_route_type
from ..base.takt import (
    _TAKT_ALTS,
    _TAKT_FLEET,
    _TAKT_MAX_LEGS,
    _TAKT_TRANSFER_MAX_WALK_M,
    _TAKT_WALK_SPEED_MPS,
    _takt_crowding_ride_mult,
    _takt_hs,
    _takt_po_seconds,
)
from .geometry import _takt_transfer_penalty_min, _transfers_match, haversine_meters


class JourneyAlternative(NamedTuple):
    """Вариант поездки с именованными полями и tuple-совместимостью.

    ``total_time_min`` — полное время варианта в минутах;
    ``legs`` — ножки ``(seq_idx, позиция посадки, позиция высадки)``.
    """
    total_time_min: float
    legs: tuple[tuple[int, int, int], ...]

_Journey = JourneyAlternative


# ===== Последовательности остановок маршрутов =====
def _optional_value(obj: Any, names: tuple[str, ...]) -> Any:
    """Читает первое доступное поле у объекта или mapping."""
    if obj is None:
        return None
    for name in names:
        if isinstance(obj, Mapping) and name in obj:
            return obj[name]
        value = getattr(obj, name, None)
        if value is not None:
            return value
    return None


def _cumulative_seconds(value: Any, count: int) -> tuple[list[float] | None, float | None]:
    """Проверяет cumT; допускает Takt-формат closed: ``n+1`` точек."""
    if value is None:
        return None, None
    try:
        values = [float(v) for v in value]
    except (TypeError, ValueError):
        return None, None
    if any(not math.isfinite(v) for v in values):
        return None, None
    if any(b < a for a, b in zip(values, values[1:])):
        return None, None
    if len(values) == count:
        return values, None
    if len(values) == count + 1:
        return values[:-1], values[-1]
    return None, None


def _derive_cumulative_seconds(stops: list[dict[str, Any]], speed_kmh: float | list[float]) -> list[float]:
    """Строит cumT fallback из геометрии и скорости ряда."""
    result = [0.0]
    for i, (a, b) in enumerate(zip(stops, stops[1:])):
        seg_speed = (
            float(speed_kmh[i]) if isinstance(speed_kmh, list) and i < len(speed_kmh)
            else float(speed_kmh) if not isinstance(speed_kmh, list)
            else 0.0
        )
        speed_mps = max(seg_speed / 3.6, 0.01)
        result.append(
            result[-1]
            + haversine_meters(
                float(a["lat"]), float(a["lon"]),
                float(b["lat"]), float(b["lon"]),
            ) / speed_mps
        )
    return result


def _segment_time_s(seq: dict[str, Any], seg_idx: int) -> float:
    """Время движения физического сегмента по cumT, без ожидания."""
    cum = seq.get("cum_t_s") or []
    if seg_idx < 0 or seg_idx >= len(cum):
        return 0.0
    if seg_idx + 1 < len(cum):
        return abs(float(cum[seg_idx + 1]) - float(cum[seg_idx]))
    if seq.get("closed") and cum:
        return max(0.0, float(seq.get("cycle_run_s", 0.0)) - float(cum[-1]))
    return 0.0

def _route_segment_indices(
    seq: dict[str, Any],
    orig_pos: int,
    dest_pos: int,
) -> list[tuple[int, bool]]:
    """Возвращает физические сегменты и направление выбранной ножки."""
    n = len(seq["stops"])
    if orig_pos == dest_pos or n < 2:
        return []
    if not seq.get("closed"):
        if orig_pos < dest_pos:
            return [(i, True) for i in range(orig_pos, dest_pos)]
        return [(i - 1, False) for i in range(orig_pos, dest_pos, -1)]
    cum = seq["cum_t_s"]
    cycle = float(seq["cycle_run_s"])
    forward_s = ((float(cum[dest_pos]) - float(cum[orig_pos])) % cycle)
    use_forward = True
    if seq.get("both_ways") and forward_s > cycle - forward_s:
        use_forward = False
    if use_forward:
        result: list[tuple[int, bool]] = []
        i = orig_pos
        while i != dest_pos:
            result.append((i, True))
            i = (i + 1) % n
        return result
    result = []
    i = orig_pos
    while i != dest_pos:
        result.append(((i - 1) % n, False))
        i = (i - 1 + n) % n
    return result

def _route_ride_time_min(seq: dict[str, Any], orig_pos: int, dest_pos: int) -> float:
    """Время поездки по формуле Takt C(...) без прохода по сегментам."""
    if orig_pos == dest_pos:
        return 0.0
    stops = seq.get("stops") or []
    n = len(stops)
    if n < 2:
        return 0.0
    cum = seq.get("cum_t_s") or []
    if orig_pos < 0 or dest_pos < 0 or orig_pos >= n or dest_pos >= n:
        return 0.0
    open_pre = seq.get("open_pre")
    if open_pre is None or len(open_pre) < n + 1:
        flags = seq.get("open", [True] * n)
        open_pre = [0]
        for value in flags:
            open_pre.append(open_pre[-1] + (1 if value else 0))
    dwell_s = float(seq.get("dwell_s", 0.0))
    speed_kmh = max(float(seq.get("speed_kmh", 0.0)), 0.01)

    def at(position: int) -> float:
        if position < len(cum):
            return float(cum[position])
        return float(position) * 3600.0 / speed_kmh

    def dwell_count(a: int, b: int) -> int:
        if b >= a:
            return max(0, int(open_pre[b]) - int(open_pre[a + 1]))
        return max(0, int(open_pre[n]) - int(open_pre[a + 1])) + max(0, int(open_pre[b]))

    def weighted(run_s: float, a: int, b: int) -> float:
        return run_s + dwell_count(a, b) * dwell_s

    if seq.get("closed"):
        cycle_s = float(seq.get("cycle_run_s", 0.0))
        if cycle_s <= 0.0:
            if len(cum) > 1:
                cycle_s = float(cum[-1])
            else:
                cycle_s = n * 3600.0 / speed_kmh
        forward_s = (at(dest_pos) - at(orig_pos)) % cycle_s
        forward = weighted(forward_s, orig_pos, dest_pos)
        if not seq.get("both_ways"):
            return forward / 60.0
        backward_s = (cycle_s - forward_s) % cycle_s
        backward = weighted(backward_s, dest_pos, orig_pos)
        return min(forward, backward) / 60.0

    run_s = abs(at(dest_pos) - at(orig_pos))
    return weighted(run_s, orig_pos, dest_pos) / 60.0 if dest_pos >= orig_pos else weighted(run_s, dest_pos, orig_pos) / 60.0


def _time_at_stop_s(seq: dict[str, Any], position: int) -> float:
    """Накопленное время до остановки для фазы пересадки."""
    open_pre = seq.get("open_pre")
    dwell_before = (
        float(open_pre[position])
        if open_pre is not None and position < len(open_pre)
        else float(position)
    )
    return (
        float(seq.get("phase_s", 0.0))
        + float(seq["cum_t_s"][position])
        + float(seq["dwell_s"]) * dwell_before
    )


def _ride_edge_time_min(
    seq: dict[str, Any],
    orig_pos: int,
    dest_pos: int,
    *,
    stop_time_min: float,
    crowd_state: Mapping[str, Mapping[tuple[int, int], float]] | None,
) -> float:
    """Стоимость line-state edge: базовое C плюс crowd/dwell feedback."""
    ride = _route_ride_time_min(seq, orig_pos, dest_pos)
    if ride <= 0.0 and orig_pos != dest_pos:
        ride = abs(dest_pos - orig_pos) * stop_time_min
    if not crowd_state or orig_pos == dest_pos:
        return max(0.0, ride)
    seg_forward = crowd_state.get("seg_forward", {})
    seg_reverse = crowd_state.get("seg_reverse", {})
    stop_extra = crowd_state.get("stop_extra", {})
    extra_s = 0.0
    selected_segments = _route_segment_indices(seq, orig_pos, dest_pos)
    prev_stop = orig_pos
    for seg_i, is_forward in selected_segments:
        loads = seg_forward if is_forward else seg_reverse
        load = float(loads.get((seq.get("_seq_idx", -1), seg_i), 0.0))
        if load > 0.0:
            extra_s += _segment_time_s(seq, seg_i) * (_takt_crowding_ride_mult(load) - 1.0)
        arrival_stop = (seg_i + 1) % len(seq["stops"]) if is_forward else seg_i % len(seq["stops"])
        if arrival_stop != prev_stop:
            extra_s += float(stop_extra.get((seq.get("_seq_idx", -1), arrival_stop), 0.0))
        prev_stop = arrival_stop
    return max(0.0, ride + extra_s / 60.0)

def _boarding_wait_min(headway_min: float | None, wait_time_min: float, wait_calc: str) -> float:
    """Ожидание на посадке: Takt Po при известном такте."""
    if headway_min is None:
        return wait_time_min
    if wait_calc == "takt":
        return _takt_po_seconds(float(headway_min)) / 60.0
    return max(wait_time_min, float(headway_min) / 2.0)



def _direction_stops(direction: Any) -> list[dict[str, Any]]:
    """Упорядоченный список остановок направления с координатами."""
    return [
        {
            "id": stop.id,
            "name": stop.name,
            "lat": stop.latitude,
            "lon": stop.longitude,
            "position": i,
        }
        for i, stop in enumerate(direction.stops)
        if stop.latitude is not None and stop.longitude is not None
    ]


def _build_route_stop_sequence(
    routes: list[RouteLike],
) -> list[dict[str, Any]]:
    """Для каждого (route, di) создаёт упорядоченный список остановок и cumT."""
    sequences: list[dict[str, Any]] = []
    for route in routes:
        if not route.ok:
            continue
        spec = vehicle_spec_for_route_type(route.route_type)
        access_m = float(spec.access_m)
        route_type_key = str(route.route_type).strip().lower()
        route_type_label = type_label(route.route_type)
        fleet_defaults = _TAKT_FLEET.get(route_type_key, {})
        explicit_route_row = _optional_value(route, ("row", "track_row"))
        route_rows = _optional_value(route, ("rows", "track_rows"))
        route_seg_cost_mul = _optional_value(route, ("segCostMul", "seg_cost_mul"))
        route_fixed_legs = _optional_value(route, ("fixedLegs", "fixed_legs"))
        route_gaps = _optional_value(route, ("gaps",))
        route_built_segs = _optional_value(route, ("builtSegs", "built_segs"))
        route_closed_segs = _optional_value(route, ("closedSegs", "closed_segs"))
        route_on_track = _optional_value(route, ("onTrack", "on_track"))
        route_row = explicit_route_row
        default_row = fleet_defaults.get("default_row")
        if route_row is None:
            route_row = default_row
        row_data = fleet_defaults.get("rows", {})
        if isinstance(row_data, Mapping) and route_row in row_data:
            row_speed_kmh = float(row_data[route_row].get("kmh", spec.speed_kmh))
        else:
            row_speed_kmh = float(spec.speed_kmh)
        closed = bool(_optional_value(route, ("closed",)))
        both_ways = bool(_optional_value(route, ("bothWays", "both_ways")))
        phase = _optional_value(route, ("phase",))
        try:
            phase_s = float(phase) * 60.0 if phase is not None else 0.0
        except (TypeError, ValueError):
            phase_s = 0.0
        for di, direction in enumerate(route.directions):
            stops = _direction_stops(direction)
            if not stops:
                continue
            explicit_cum = _optional_value(
                direction,
                ("cumT", "cum_t", "cumulative_time_s", "cum_time_s"),
            )
            if explicit_cum is None:
                route_cum = _optional_value(
                    route,
                    ("cumT", "cum_t", "cumulative_time_s", "cum_time_s"),
                )
                if (
                    isinstance(route_cum, (list, tuple))
                    and route_cum
                    and isinstance(route_cum[0], (list, tuple))
                ):
                    explicit_cum = route_cum[di]
                else:
                    explicit_cum = route_cum
            cum_t_s, explicit_cycle_s = _cumulative_seconds(
                explicit_cum, len(stops)
            )
            direction_rows = _optional_value(direction, ("rows", "track_rows"))
            if direction_rows is None:
                direction_rows = route_rows
            segment_count = len(stops) if closed else max(0, len(stops) - 1)
            row_speed_profile: list[float] = []
            for seg_i in range(segment_count):
                row_i = route_row
                if isinstance(direction_rows, (list, tuple)) and seg_i < len(direction_rows) and direction_rows[seg_i] is not None:
                    row_i = direction_rows[seg_i]
                elif isinstance(direction_rows, Mapping) and seg_i in direction_rows:
                    row_i = direction_rows[seg_i]
                data = row_data.get(row_i) if isinstance(row_data, Mapping) else None
                row_speed_profile.append(float(data.get("kmh", spec.speed_kmh)) if isinstance(data, Mapping) else float(spec.speed_kmh))
            if not row_speed_profile:
                row_speed_profile = [row_speed_kmh]
            if cum_t_s is None:
                cum_t_s = _derive_cumulative_seconds(stops, row_speed_profile)
                explicit_cycle_s = None
            open_values = _optional_value(
                direction, ("openStops", "open_stops")
            )
            if open_values is None:
                open_values = _optional_value(route, ("openStops", "open_stops"))
            if open_values is None:
                open_values = [True] * len(stops)
            try:
                open_values = [
                    bool(open_values[i]) if i < len(open_values) else True
                    for i in range(len(stops))
                ]
            except (TypeError, IndexError):
                open_values = [True] * len(stops)
            open_pre = [0]
            for value in open_values:
                open_pre.append(open_pre[-1] + (1 if value else 0))
            cycle_run_s = (
                float(explicit_cycle_s)
                if explicit_cycle_s is not None
                else float(cum_t_s[-1])
            )
            if closed and len(stops) >= 2 and explicit_cycle_s is None:
                cycle_run_s += (
                    haversine_meters(
                        stops[-1]["lat"], stops[-1]["lon"],
                        stops[0]["lat"], stops[0]["lon"],
                    )
                    / max(row_speed_profile[-1] / 3.6, 0.01)
                )
            sequences.append(
                {
                    "_seq_idx": len(sequences),
                    "route_id": route.route_id,
                    "route_name": route.name,
                    "route_type": route_type_label,
                    "route_type_key": route_type_key,
                    "access_m": access_m,
                    "row": route_row,
                    "row_explicit": explicit_route_row is not None,
                    "rows": _optional_value(direction, ("rows", "track_rows")) if _optional_value(direction, ("rows", "track_rows")) is not None else route_rows,
                    "seg_cost_mul": _optional_value(direction, ("segCostMul", "seg_cost_mul")) if _optional_value(direction, ("segCostMul", "seg_cost_mul")) is not None else route_seg_cost_mul,
                    "fixed_legs": _optional_value(direction, ("fixedLegs", "fixed_legs")) if _optional_value(direction, ("fixedLegs", "fixed_legs")) is not None else route_fixed_legs,
                    "gaps": _optional_value(direction, ("gaps",)) if _optional_value(direction, ("gaps",)) is not None else route_gaps,
                    "built_segs": _optional_value(direction, ("builtSegs", "built_segs")) if _optional_value(direction, ("builtSegs", "built_segs")) is not None else route_built_segs,
                    "closed_segs": _optional_value(direction, ("closedSegs", "closed_segs")) if _optional_value(direction, ("closedSegs", "closed_segs")) is not None else route_closed_segs,
                    "on_track": _optional_value(direction, ("onTrack", "on_track")) if _optional_value(direction, ("onTrack", "on_track")) is not None else route_on_track,
                    "di": di,
                    "direction_name": direction.name or f"Направление {di + 1}",
                    "stops": stops,
                    "cum_t_s": cum_t_s,
                    "cycle_run_s": cycle_run_s,
                    "dwell_s": float(spec.dwell_s),
                    "speed_kmh": float(row_speed_kmh),
                    "closed": closed,
                    "both_ways": both_ways,
                    "phase_s": phase_s,
                    "open": open_values,
                    "open_pre": open_pre,
                }
            )
    return sequences


# ===== Прямые варианты =====


def _best_direct_spans(
    origins: list[tuple[int, int, int]],
    destinations: list[tuple[int, int, int]],
) -> dict[int, tuple[int, int, int]]:
    """Кандидат на (rid, di) с минимальным числом межостановочных шагов.

    JS-граф допускает переход между любыми двумя открытыми остановками одной
    последовательности; порядок позиций поэтому не фиксируется.
    """
    best_span: dict[int, tuple[int, int, int]] = {}
    for orig_seq_idx, _orig_stop_idx, orig_pos in origins:
        for dest_seq_idx, _dest_stop_idx, dest_pos in destinations:
            if orig_seq_idx == dest_seq_idx and orig_pos != dest_pos:
                span = abs(dest_pos - orig_pos)
                prev = best_span.get(orig_seq_idx)
                if prev is None or span < prev[0]:
                    best_span[orig_seq_idx] = (span, orig_pos, dest_pos)
    return best_span


def _direct_journeys(
    best_span: dict[int, tuple[int, int, int]],
    route_stop_sequences: list[dict[str, Any]],
    *,
    stop_time_min: float,
    walk_to_stop_min: float,
    wait_time_min: float,
    seq_headway_min: Mapping[int, float] | None,
    wait_calc: str,
) -> list[_Journey]:
    """Прямые варианты с реальным временем движения маршрута."""
    journeys: list[_Journey] = []
    for seq_idx, (_span, orig_pos, dest_pos) in best_span.items():
        seq = route_stop_sequences[seq_idx]
        ride_min = _route_ride_time_min(seq, orig_pos, dest_pos)
        if ride_min <= 0.0:
            ride_min = (dest_pos - orig_pos) * stop_time_min
        headway = (
            seq_headway_min.get(seq_idx)
            if seq_headway_min is not None
            else None
        )
        wait_min = _boarding_wait_min(headway, wait_time_min, wait_calc)
        journeys.append(
            JourneyAlternative(
                ride_min + walk_to_stop_min + wait_min,
                ((seq_idx, orig_pos, dest_pos),),
            )
        )
    return journeys


# ===== Варианты с одной пересадкой =====


def _board_positions(origins: list[tuple[int, int, int]]) -> dict[int, int]:
    """Минимальная позиция посадки по каждому seq (первая посадка зоны)."""
    result: dict[int, int] = {}
    for seq_idx, _stop_idx, pos in origins:
        cur = result.get(seq_idx)
        if cur is None or pos < cur:
            result[seq_idx] = pos
    return result


def _alight_positions(
    destinations: list[tuple[int, int, int]],
) -> dict[int, int]:
    """Максимальная позиция высадки по каждому seq (последняя высадка зоны)."""
    result: dict[int, int] = {}
    for seq_idx, _stop_idx, pos in destinations:
        cur = result.get(seq_idx)
        if cur is None or pos > cur:
            result[seq_idx] = pos
    return result


def _transfer_pairs(
    seq_a: int,
    a_pos: int,
    seq_b: int,
    d_pos: int,
    route_stop_sequences: list[dict[str, Any]],
    transfer_radius_m: float,
) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
    """Пары остановок (ta, tb), пригодные для пересадки A→B.

    A должна проехать ``ta`` после посадки (``ta.position > a_pos``),
    B — ``tb`` до высадки (``tb.position < d_pos``); остановки должны
    совпадать по id/радиусу (``_transfers_match``).
    """
    stops_a = route_stop_sequences[seq_a]["stops"]
    stops_b = route_stop_sequences[seq_b]["stops"]
    for ta in stops_a:
        if ta["position"] <= a_pos:
            continue
        for tb in stops_b:
            if tb["position"] >= d_pos:
                continue
            if _transfers_match(ta, tb, transfer_radius_m):
                yield ta, tb


def _scheduled_transfer_wait_min(
    seq_a: int,
    seq_b: int,
    ta: dict[str, Any],
    tb: dict[str, Any],
    *,
    stop_time_min: float,
    route_stop_sequences: list[dict[str, Any]],
    seq_headway_min: Mapping[int, float],
    seq_jitter_s: Mapping[int, float],
) -> float:
    """Ожидание пересадки по расписанию Takt (``jo``/``hs``) в минутах.

    Считает фазы остановок и walk-время между ними, затем — координацию
    двух линий через ``_takt_hs``.
    """
    default_wait_s = _takt_po_seconds(float(seq_headway_min[seq_b]))
    walk_m = min(
        haversine_meters(
            float(ta["lat"]),
            float(ta["lon"]),
            float(tb["lat"]),
            float(tb["lon"]),
        ),
        _TAKT_TRANSFER_MAX_WALK_M,
    )
    walk_s = walk_m / _TAKT_WALK_SPEED_MPS
    cum_from_s = _time_at_stop_s(
        route_stop_sequences[seq_a], int(ta["position"])
    )
    cum_to_s = _time_at_stop_s(
        route_stop_sequences[seq_b], int(tb["position"])
    )
    return (
        _takt_hs(
            float(seq_headway_min[seq_a]),
            float(seq_headway_min[seq_b]),
            cum_from_s,
            cum_to_s,
            walk_s,
            float(seq_jitter_s.get(seq_a, 0.0)),
            default_wait_s,
        )
        / 60.0
    )


def _transfer_wait_min(
    seq_a: int,
    seq_b: int,
    ta: dict[str, Any],
    tb: dict[str, Any],
    *,
    stop_time_min: float,
    wait_time_min: float,
    transfer_wait_min: float | None,
    seq_headway_min: Mapping[int, float] | None,
    seq_jitter_s: Mapping[int, float] | None,
    route_stop_sequences: list[dict[str, Any]],
) -> float:
    """Ожидание на пересадке: по расписанию (если заданы headway/jitter),
    иначе — ``transfer_wait_min`` (или обычное ``wait_time_min``).
    """
    if (
        seq_headway_min is not None
        and seq_jitter_s is not None
        and seq_a in seq_headway_min
        and seq_b in seq_headway_min
    ):
        return _scheduled_transfer_wait_min(
            seq_a,
            seq_b,
            ta,
            tb,
            stop_time_min=stop_time_min,
            route_stop_sequences=route_stop_sequences,
            seq_headway_min=seq_headway_min,
            seq_jitter_s=seq_jitter_s,
        )
    return wait_time_min if transfer_wait_min is None else transfer_wait_min


def _transfer_penalty(
    ta: dict[str, Any],
    tb: dict[str, Any],
    *,
    transfer_penalty_min: float,
    transfer_penalty_calc: str,
) -> float:
    """Штраф за пересадку: фиксированный либо по расстоянию (Takt)."""
    if transfer_penalty_calc == "fixed":
        return transfer_penalty_min
    return _takt_transfer_penalty_min(
        float(ta["lat"]),
        float(ta["lon"]),
        float(tb["lat"]),
        float(tb["lon"]),
    )


def _transfer_leg_time_min(
    seq_a: dict[str, Any],
    seq_b: dict[str, Any],
    a_pos: int,
    ta: dict[str, Any],
    tb: dict[str, Any],
    d_pos: int,
    *,
    stop_time_min: float,
    first_wait_min: float,
    transfer_wait: float,
    transfer_penalty_min: float,
    transfer_penalty_calc: str,
) -> float:
    """Полное время пересадочного варианта (без walk-to-stop)."""
    ride_a = _route_ride_time_min(seq_a, a_pos, int(ta["position"]))
    ride_b = _route_ride_time_min(seq_b, int(tb["position"]), d_pos)
    if ride_a <= 0.0:
        ride_a = max(0, int(ta["position"]) - a_pos) * stop_time_min
    if ride_b <= 0.0:
        ride_b = max(0, d_pos - int(tb["position"])) * stop_time_min
    return (
        ride_a
        + ride_b
        + _transfer_penalty(
            ta,
            tb,
            transfer_penalty_min=transfer_penalty_min,
            transfer_penalty_calc=transfer_penalty_calc,
        )
        + first_wait_min
        + transfer_wait
    )


def _transfer_journeys(
    board_by_seq: Mapping[int, int],
    alight_by_seq: Mapping[int, int],
    route_stop_sequences: list[dict[str, Any]],
    *,
    stop_time_min: float,
    wait_time_min: float,
    walk_to_stop_min: float,
    transfer_penalty_min: float,
    transfer_wait_min: float | None,
    transfer_radius_m: float,
    transfer_penalty_calc: str,
    seq_headway_min: Mapping[int, float] | None,
    seq_jitter_s: Mapping[int, float] | None,
    wait_calc: str,
) -> list[_Journey]:
    """Все варианты поездки с одной пересадкой между разными маршрутами."""
    journeys: list[_Journey] = []
    for seq_a, a_pos in board_by_seq.items():
        for seq_b, d_pos in alight_by_seq.items():
            if seq_a == seq_b:
                continue
            for ta, tb in _transfer_pairs(
                seq_a,
                a_pos,
                seq_b,
                d_pos,
                route_stop_sequences,
                transfer_radius_m,
            ):
                transfer_wait = _transfer_wait_min(
                    seq_a,
                    seq_b,
                    ta,
                    tb,
                    stop_time_min=stop_time_min,
                    wait_time_min=wait_time_min,
                    transfer_wait_min=transfer_wait_min,
                    seq_headway_min=seq_headway_min,
                    seq_jitter_s=seq_jitter_s,
                    route_stop_sequences=route_stop_sequences,
                )
                headway_a = (
                    seq_headway_min.get(seq_a)
                    if seq_headway_min is not None
                    else None
                )
                first_wait = _boarding_wait_min(
                    headway_a, wait_time_min, wait_calc
                )
                time_min = _transfer_leg_time_min(
                    route_stop_sequences[seq_a],
                    route_stop_sequences[seq_b],
                    a_pos,
                    ta,
                    tb,
                    d_pos,
                    stop_time_min=stop_time_min,
                    first_wait_min=first_wait,
                    transfer_wait=transfer_wait,
                    transfer_penalty_min=transfer_penalty_min,
                    transfer_penalty_calc=transfer_penalty_calc,
                )
                journeys.append(
                    JourneyAlternative(
                        time_min + walk_to_stop_min,
                        (
                            (seq_a, a_pos, ta["position"]),
                            (seq_b, tb["position"], d_pos),
                        ),
                    )
                )
    return journeys



def _destination_positions(
    seq_idx: int,
    destinations: list[tuple[int, int, int]],
    current_pos: int = -1,
    closed: bool = False,
) -> list[int]:
    """Позиции высадки; закрытый маршрут допускает переход через нулевую остановку."""
    return sorted(
        {
            pos
            for seq, _stop, pos in destinations
            if seq == seq_idx
            and (pos != current_pos if closed else pos != current_pos)
        }
    )


def _transfer_targets(
    seq_a: int,
    current_pos: int,
    route_stop_sequences: list[dict[str, Any]],
    excluded: set[int],
    transfer_radius_m: float,
) -> Iterator[tuple[int, dict[str, Any], dict[str, Any]]]:
    """Генерирует ближайший допустимый переход A→B для каждой другой линии.

    Как и JS ``Et``, для каждой остановки A выбирается только ближайшая
    остановка каждой другой линии в пределах радиуса пересадки. Направление
    внутри последовательности линии не ограничивается индексом остановки.
    """
    for ta in route_stop_sequences[seq_a]["stops"]:
        for seq_b, data_b in enumerate(route_stop_sequences):
            if seq_b == seq_a or seq_b in excluded:
                continue
            best_tb: dict[str, Any] | None = None
            best_dist = float(transfer_radius_m) + 1.0
            for tb in data_b["stops"]:
                distance_m = haversine_meters(
                    float(ta["lat"]),
                    float(ta["lon"]),
                    float(tb["lat"]),
                    float(tb["lon"]),
                )
                if distance_m > transfer_radius_m + 1e-9:
                    continue
                if (
                    best_tb is None
                    or distance_m < best_dist - 1e-9
                    or (
                        math.isclose(distance_m, best_dist, rel_tol=0.0, abs_tol=1e-9)
                        and int(tb["position"]) > int(best_tb["position"])
                    )
                ):
                    best_tb = tb
                    best_dist = distance_m
            if best_tb is not None:
                yield seq_b, ta, best_tb


def _build_transfer_edge_index(
    route_stop_sequences: list[dict[str, Any]],
    transfer_radius_m: float,
) -> dict[tuple[int, int], tuple[tuple[int, dict[str, Any], dict[str, Any]], ...]]:
    """Предвычисляет ближайшие transfer edges для каждой source-stop."""
    entries: list[tuple[float, float, int, dict[str, Any]]] = []
    for seq_idx, seq in enumerate(route_stop_sequences):
        for stop in seq.get("stops", []):
            entries.append((float(stop["lat"]), float(stop["lon"]), seq_idx, stop))
    if not entries:
        return {}
    ref_lat = sum(x[0] for x in entries) / len(entries)
    lat_scale = 111_320.0
    lon_scale = lat_scale * max(math.cos(math.radians(ref_lat)), 0.2)
    points = [(lat * lat_scale, lon * lon_scale) for lat, lon, _seq, _stop in entries]
    tree = cKDTree(points)
    result: dict[tuple[int, int], tuple[tuple[int, dict[str, Any], dict[str, Any]], ...]] = {}
    query_r = float(transfer_radius_m)
    for seq_a, seq in enumerate(route_stop_sequences):
        for ta in seq.get("stops", []):
            pos = int(ta["position"])
            x = float(ta["lat"]) * lat_scale
            y = float(ta["lon"]) * lon_scale
            best_by_line: dict[int, tuple[float, dict[str, Any]]] = {}
            for flat_i in tree.query_ball_point((x, y), r=query_r):
                seq_b = entries[flat_i][2]
                tb = entries[flat_i][3]
                if seq_b == seq_a:
                    continue
                distance_m = haversine_meters(
                    float(ta["lat"]), float(ta["lon"]),
                    float(tb["lat"]), float(tb["lon"]),
                )
                if distance_m > query_r + 1e-9:
                    continue
                prior = best_by_line.get(seq_b)
                if (prior is None or distance_m < prior[0] - 1e-9 or
                    (math.isclose(distance_m, prior[0], rel_tol=0.0, abs_tol=1e-9)
                     and int(tb["position"]) > int(prior[1]["position"]))):
                    best_by_line[seq_b] = (distance_m, tb)
            result[(seq_a, pos)] = tuple(
                (seq_b, ta, tb) for seq_b, (_distance, tb) in sorted(best_by_line.items())
            )
    return result

def _dedupe_journeys(
    journeys: list[_Journey],
    max_alternatives: int,
    route_stop_sequences: list[dict[str, Any]],
) -> list[_Journey]:
    """Оставляет до ``max_alternatives`` кандидатов как в Takt ``ri``.

    Первый кандидат определяется минимальным полным временем. Дополнительные
    кандидаты принимаются только если их первая посадка находится не дальше
    150 м от первой посадки лучшего кандидата (``ni`` в JS-движке Takt).
    Это заменяет прежнее приближение через 25% + 120 с.
    """
    if not journeys:
        return []
    ordered = sorted(journeys, key=lambda item: (item[0], item[1]))
    first = ordered[0]
    selected: list[_Journey] = [first]
    seen = {first[1]}
    first_leg = first.legs[0]
    first_seq, first_pos, _ = first_leg
    first_stop = route_stop_sequences[first_seq]["stops"][first_pos]
    for journey in ordered[1:]:
        if len(selected) >= max(1, min(int(max_alternatives), _TAKT_ALTS)):
            break
        if journey[1] in seen:
            continue
        leg = journey.legs[0]
        seq_idx, pos, _ = leg
        stop = route_stop_sequences[seq_idx]["stops"][pos]
        distance_m = haversine_meters(
            float(first_stop["lat"]),
            float(first_stop["lon"]),
            float(stop["lat"]),
            float(stop["lon"]),
        )
        if distance_m > 150.0 + 1e-9:
            continue
        selected.append(journey)
        seen.add(journey[1])
    return selected

def _enumerate_journeys(
    origins: list[tuple[int, int, int]],
    destinations: list[tuple[int, int, int]],
    route_stop_sequences: list[dict[str, Any]],
    *,
    stop_time_min: float,
    wait_time_min: float,
    walk_to_stop_min: float,
    transfer_penalty_min: float,
    transfer_wait_min: float | None,
    transfer_radius_m: float,
    transfer_penalty_calc: str,
    seq_headway_min: Mapping[int, float] | None,
    seq_jitter_s: Mapping[int, float] | None,
    wait_calc: str,
    max_legs: int,
    max_alternatives: int,
    crowd_state: Mapping[str, Mapping[tuple[int, int], float]] | None = None,
) -> list[_Journey]:
    """Детерминированный shortest-path поиск по состояниям stop/line.

    В отличие от прежнего beam-search здесь нет произвольного лимита ширины.
    Состояние хранит текущую линию, остановку, число пересадок и уже
    использованные линии; соседями являются оба направления движения по
    sequence и ближайшие transfer-edge по каждой другой линии.
    """
    max_legs = min(max(1, int(max_legs)), _TAKT_MAX_LEGS)
    if not origins or not destinations or not route_stop_sequences:
        return []

    destination_by_seq: dict[int, set[int]] = {}
    for seq_idx, _stop_idx, pos in destinations:
        destination_by_seq.setdefault(seq_idx, set()).add(int(pos))

    # (cost, seq_idx, pos, transfers, leg_start, legs, used)
    # used не влияет на стоимость, но предотвращает циклическое повторное
    # использование уже пройденной линии, как старый transfer enumerator.
    heap: list[tuple[float, int, int, int, int, tuple[tuple[int,int,int], ...], frozenset[int]]] = []
    best: dict[tuple[int, int, int, int, frozenset[int]], float] = {}
    first_wait_by_seq: dict[int, float] = {}
    for seq_idx, _stop_idx, _orig_pos in origins:
        if seq_idx in first_wait_by_seq:
            continue
        headway = seq_headway_min.get(seq_idx) if seq_headway_min is not None else None
        first_wait_by_seq[seq_idx] = _boarding_wait_min(headway, wait_time_min, wait_calc)

    transfer_index = _build_transfer_edge_index(route_stop_sequences, transfer_radius_m)
    def cached_transfer_targets(seq_idx: int, pos: int) -> tuple[tuple[int, dict[str, Any], dict[str, Any]], ...]:
        return transfer_index.get((seq_idx, pos), ())

    for seq_idx, _stop_idx, orig_pos in origins:
        if seq_idx < 0 or seq_idx >= len(route_stop_sequences):
            continue
        key = (seq_idx, int(orig_pos), 0, int(orig_pos), frozenset((seq_idx,)))
        state = (0.0, seq_idx, int(orig_pos), 0, int(orig_pos), tuple(), frozenset((seq_idx,)))
        prior = best.get(key)
        if prior is None:
            best[key] = 0.0
            heapq.heappush(heap, state)

    journeys: list[_Journey] = []
    seen_journeys: set[tuple[tuple[int, int, int], ...]] = set()

    while heap:
        cost, seq_idx, pos, transfers, leg_start, legs, used = heapq.heappop(heap)
        key = (seq_idx, pos, transfers, leg_start, used)
        if cost > best.get(key, math.inf) + 1e-9:
            continue
        seq = route_stop_sequences[seq_idx]

        # Terminate at every destination stop reachable on this line.
        for d_pos in destination_by_seq.get(seq_idx, ()):
            if d_pos == pos and not legs and d_pos == leg_start:
                continue
            ride = _ride_edge_time_min(
                seq, leg_start, d_pos,
                stop_time_min=stop_time_min,
                crowd_state=crowd_state,
            )
            final_legs = legs + ((seq_idx, leg_start, d_pos),)
            if not final_legs:
                continue
            first_seq = final_legs[0][0]
            total = cost + ride + walk_to_stop_min + first_wait_by_seq.get(first_seq, 0.0)
            signature = final_legs
            if signature not in seen_journeys:
                seen_journeys.add(signature)
                journeys.append(JourneyAlternative(total, final_legs))

        # Full same-line edges are evaluated lazily when terminating at a
        # destination or transferring. Because every pair of stops is linked
        # in Takt's `we` graph, creating intermediate same-line states would
        # only duplicate those direct edges and inflate the search space.
        if transfers >= max_legs - 1:
            continue

        # Transfer from the current stop. _transfer_targets also preserves
        # the Takt nearest-stop-per-target-line rule.
        for seq_b, ta, tb in cached_transfer_targets(seq_idx, pos):
            if seq_b in used:
                continue
            ta_pos = int(ta["position"])
            tb_pos = int(tb["position"])
            ride_to_transfer = _ride_edge_time_min(
                seq, leg_start, ta_pos,
                stop_time_min=stop_time_min,
                crowd_state=crowd_state,
            )
            transfer_wait = _transfer_wait_min(
                seq_idx, seq_b, ta, tb,
                stop_time_min=stop_time_min,
                wait_time_min=wait_time_min,
                transfer_wait_min=transfer_wait_min,
                seq_headway_min=seq_headway_min,
                seq_jitter_s=seq_jitter_s,
                route_stop_sequences=route_stop_sequences,
            )
            penalty = _transfer_penalty(
                ta, tb,
                transfer_penalty_min=transfer_penalty_min,
                transfer_penalty_calc=transfer_penalty_calc,
            )
            closed_legs = legs + ((seq_idx, leg_start, ta_pos),)
            new_cost = cost + ride_to_transfer + penalty + transfer_wait
            nkey = (seq_b, tb_pos, transfers + 1, tb_pos, used | {seq_b})
            if new_cost + 1e-12 < best.get(nkey, math.inf):
                best[nkey] = new_cost
                heapq.heappush(heap, (
                    new_cost, seq_b, tb_pos, transfers + 1, tb_pos,
                    closed_legs, used | {seq_b}
                ))

    return _dedupe_journeys(
        journeys, max_alternatives, route_stop_sequences
    )

# ===== Главная точка входа =====


def build_journeys(
    origins: list[tuple[int, int, int]],
    destinations: list[tuple[int, int, int]],
    route_stop_sequences: list[dict[str, Any]],
    *,
    stop_time_min: float,
    wait_time_min: float,
    walk_to_stop_min: float,
    transfer_penalty_min: float,
    transfer_wait_min: float | None = None,
    transfer_radius_m: float,
    max_transfers: int,
    transfer_penalty_calc: str = "takt",
    seq_headway_min: Mapping[int, float] | None = None,
    seq_jitter_s: Mapping[int, float] | None = None,
    wait_calc: str = "takt",
    crowd_state: Mapping[str, Mapping[tuple[int, int], float]] | None = None,
) -> list[_Journey]:
    """Возвращает до трёх вариантов поездки с максимумом четырёх ножек."""
    max_legs = min(_TAKT_MAX_LEGS, max(1, int(max_transfers) + 1))
    return _enumerate_journeys(
        origins,
        destinations,
        route_stop_sequences,
        stop_time_min=stop_time_min,
        wait_time_min=wait_time_min,
        walk_to_stop_min=walk_to_stop_min,
        transfer_penalty_min=transfer_penalty_min,
        transfer_wait_min=transfer_wait_min,
        transfer_radius_m=transfer_radius_m,
        transfer_penalty_calc=transfer_penalty_calc,
        seq_headway_min=seq_headway_min,
        seq_jitter_s=seq_jitter_s,
        wait_calc=wait_calc,
        max_legs=max_legs,
        max_alternatives=min(_TAKT_ALTS, 3),
        crowd_state=crowd_state,
    )

