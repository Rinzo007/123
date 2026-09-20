"""Подготовка маршрутов к расчёту: последовательности остановок и варианты поездки.

Здесь собрано построение упорядоченных списков остановок направлений
и ограниченное перечисление вариантов поездки между зонами: прямые пути,
а также пути до 4 ножек с пересадками и максимум тремя альтернативами.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
import math
from typing import Any

from ...models import RouteLike
from ...support import type_label
from ..base.models import vehicle_spec_for_route_type
from ..base.takt import (
    _TAKT_ALTS,
    _TAKT_ALT_DETOUR_FACTOR,
    _TAKT_ALT_DETOUR_FIXED_S,
    _TAKT_MAX_LEGS,
    _TAKT_TRANSFER_MAX_WALK_M,
    _TAKT_WALK_SPEED_MPS,
    _takt_hs,
    _takt_po_seconds,
)
from .geometry import _takt_transfer_penalty_min, _transfers_match, haversine_meters


# Один вариант поездки: (полное время в минутах, кортеж ножек).
# Ножка — (seq_idx, позиция посадки, позиция высадки).
_Journey = tuple[float, tuple[tuple[int, int, int], ...]]


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


def _derive_cumulative_seconds(stops: list[dict[str, Any]], speed_kmh: float) -> list[float]:
    """Строит cumT fallback из геометрии и скорости ряда."""
    speed_mps = max(float(speed_kmh) / 3.6, 0.01)
    result = [0.0]
    for a, b in zip(stops, stops[1:]):
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
    """Время поездки между остановками с семантикой Takt C(...)."""
    if orig_pos == dest_pos:
        return 0.0
    segments = _route_segment_indices(seq, orig_pos, dest_pos)
    if not segments:
        return 0.0
    movement_s = sum(
        _segment_time_s(seq, seg_idx) for seg_idx, _forward in segments
    )
    dwell_count = 0
    for offset, (seg_idx, is_forward) in enumerate(segments[:-1]):
        arrival = (
            (seg_idx + 1) % len(seq["stops"])
            if is_forward
            else seg_idx
        )
        if seq.get("open", [True] * len(seq["stops"]))[arrival]:
            dwell_count += 1
    return (
        movement_s + dwell_count * float(seq["dwell_s"])
    ) / 60.0


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
            if cum_t_s is None:
                cum_t_s = _derive_cumulative_seconds(stops, spec.speed_kmh)
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
                    / max(float(spec.speed_kmh) / 3.6, 0.01)
                )
            sequences.append(
                {
                    "_seq_idx": len(sequences),
                    "route_id": route.route_id,
                    "route_name": route.name,
                    "route_type": route_type_label,
                    "route_type_key": route_type_key,
                    "access_m": access_m,
                    "di": di,
                    "direction_name": direction.name or f"Направление {di + 1}",
                    "stops": stops,
                    "cum_t_s": cum_t_s,
                    "cycle_run_s": cycle_run_s,
                    "dwell_s": float(spec.dwell_s),
                    "speed_kmh": float(spec.speed_kmh),
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
    """Один «кандидат» на (rid, di): пара (orig_pos, dest_pos) с минимальным
    временем проезда, чтобы маршрут не дублировался для каждой пары
    остановок зоны (дедупликация по маршруту).
    """
    best_span: dict[int, tuple[int, int, int]] = {}
    for orig_seq_idx, _orig_stop_idx, orig_pos in origins:
        for dest_seq_idx, _dest_stop_idx, dest_pos in destinations:
            if orig_seq_idx == dest_seq_idx and orig_pos < dest_pos:
                span = dest_pos - orig_pos
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
            (
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
                    (
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
            and (pos != current_pos if closed else pos > current_pos)
        }
    )


def _transfer_targets(
    seq_a: int,
    current_pos: int,
    route_stop_sequences: list[dict[str, Any]],
    excluded: set[int],
    transfer_radius_m: float,
) -> Iterator[tuple[int, dict[str, Any], dict[str, Any]]]:
    """Генерирует все допустимые переходы A→B после current_pos."""
    closed_a = bool(route_stop_sequences[seq_a].get("closed"))
    for ta in route_stop_sequences[seq_a]["stops"]:
        if int(ta["position"]) == current_pos:
            continue
        if not closed_a and int(ta["position"]) <= current_pos:
            continue
        for seq_b, data_b in enumerate(route_stop_sequences):
            if seq_b == seq_a or seq_b in excluded:
                continue
            for tb in data_b["stops"]:
                if _transfers_match(ta, tb, transfer_radius_m):
                    yield seq_b, ta, tb


def _dedupe_journeys(
    journeys: list[_Journey],
    max_alternatives: int,
) -> list[_Journey]:
    """Оставляет лучшие уникальные варианты в окне Takt detour + 120 секунд."""
    if not journeys:
        return []
    best = min(j[0] for j in journeys)
    threshold = (
        best * _TAKT_ALT_DETOUR_FACTOR + _TAKT_ALT_DETOUR_FIXED_S / 60.0
    )
    seen: set[tuple[tuple[int, int, int], ...]] = set()
    result: list[_Journey] = []
    for journey in sorted(journeys, key=lambda item: (item[0], item[1])):
        if journey[0] > threshold + 1e-9:
            continue
        if journey[1] in seen:
            continue
        seen.add(journey[1])
        result.append(journey)
        if len(result) >= max(1, max_alternatives):
            break
    return result


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
) -> list[_Journey]:
    """Ограниченный поиск 0..4 ножек."""
    max_legs = min(max(1, int(max_legs)), _TAKT_MAX_LEGS)
    board = _board_positions(origins)
    journeys: list[_Journey] = []
    frontier: list[
        tuple[
            float,
            int,
            int,
            tuple[tuple[int, int, int], ...],
            frozenset[int],
        ]
    ] = []

    for seq_idx, orig_pos in board.items():
        headway = (
            seq_headway_min.get(seq_idx)
            if seq_headway_min is not None
            else None
        )
        first_wait = _boarding_wait_min(headway, wait_time_min, wait_calc)
        for d_pos in _destination_positions(
            seq_idx,
            destinations,
            orig_pos,
            closed=bool(route_stop_sequences[seq_idx].get("closed")),
        ):
            ride = _route_ride_time_min(
                route_stop_sequences[seq_idx], orig_pos, d_pos
            )
            if ride <= 0.0:
                ride = (d_pos - orig_pos) * stop_time_min
            journeys.append(
                (
                    ride + first_wait + walk_to_stop_min,
                    ((seq_idx, orig_pos, d_pos),),
                )
            )
        frontier.append(
            (first_wait, seq_idx, orig_pos, tuple(), frozenset((seq_idx,)))
        )

    beam_size = max(256, len(route_stop_sequences) * 8)
    while frontier:
        next_frontier = []
        for base_cost, seq_a, current_pos, legs, used in frontier:
            if len(legs) >= max_legs - 1:
                continue
            for seq_b, ta, tb in _transfer_targets(
                seq_a,
                current_pos,
                route_stop_sequences,
                set(used),
                transfer_radius_m,
            ):
                ta_pos = int(ta["position"])
                tb_pos = int(tb["position"])
                ride_a = _route_ride_time_min(
                    route_stop_sequences[seq_a], current_pos, ta_pos
                )
                if ride_a <= 0.0:
                    ride_a = max(0, ta_pos - current_pos) * stop_time_min
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
                penalty = _transfer_penalty(
                    ta,
                    tb,
                    transfer_penalty_min=transfer_penalty_min,
                    transfer_penalty_calc=transfer_penalty_calc,
                )
                new_cost = base_cost + ride_a + penalty + transfer_wait
                new_legs = legs + ((seq_a, current_pos, ta_pos),)

                for d_pos in _destination_positions(
                    seq_b,
                    destinations,
                    tb_pos,
                    closed=bool(route_stop_sequences[seq_b].get("closed")),
                ):
                    ride_b = _route_ride_time_min(
                        route_stop_sequences[seq_b], tb_pos, d_pos
                    )
                    if ride_b <= 0.0:
                        ride_b = (d_pos - tb_pos) * stop_time_min
                    journeys.append(
                        (
                            new_cost + ride_b + walk_to_stop_min,
                            new_legs + ((seq_b, tb_pos, d_pos),),
                        )
                    )

                if len(new_legs) < max_legs - 1:
                    next_frontier.append(
                        (
                            new_cost,
                            seq_b,
                            tb_pos,
                            new_legs,
                            used | {seq_b},
                        )
                    )
        if not next_frontier:
            break
        next_frontier.sort(key=lambda state: (state[0], state[3]))
        frontier = next_frontier[:beam_size]

    return _dedupe_journeys(journeys, max_alternatives)


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
    )

