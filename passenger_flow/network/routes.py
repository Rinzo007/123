"""Подготовка маршрутов к расчёту: последовательности остановок и варианты поездки.

Здесь собрано построение упорядоченных списков остановок направлений
и перечисление вариантов поездки между зонами: прямого (одна ножка,
лучший по времени для каждого направления маршрута) и с одной пересадкой
(первая ножка должна проехать точку совмещения после посадки, вторая —
до высадки).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

from ...models import RouteLike
from ...support import type_label
from ..base.models import vehicle_spec_for_route_type
from ..base.takt import (
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
    """Для каждого (route, di) создаёт упорядоченный список остановок."""
    sequences: list[dict[str, Any]] = []
    for route in routes:
        if not route.ok:
            continue
        access_m = float(vehicle_spec_for_route_type(route.route_type).access_m)
        route_type_key = str(route.route_type).strip().lower()
        route_type_label = type_label(route.route_type)
        for di, direction in enumerate(route.directions):
            stops = _direction_stops(direction)
            if not stops:
                continue
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
    *,
    stop_time_min: float,
    walk_to_stop_min: float,
    wait_time_min: float,
) -> list[_Journey]:
    """Прямые варианты поездки (одна ножка) по лучшим спанам."""
    journeys: list[_Journey] = []
    for seq_idx, (_span, orig_pos, dest_pos) in best_span.items():
        time_min = (dest_pos - orig_pos) * stop_time_min
        journeys.append(
            (
                time_min + walk_to_stop_min + wait_time_min,
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
    cum_from_s = float(ta["position"]) * stop_time_min * 60.0
    cum_to_s = float(tb["position"]) * stop_time_min * 60.0
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
    a_pos: int,
    ta: dict[str, Any],
    tb: dict[str, Any],
    d_pos: int,
    *,
    stop_time_min: float,
    wait_time_min: float,
    transfer_wait: float,
    transfer_penalty_min: float,
    transfer_penalty_calc: str,
) -> float:
    """Полное время пересадочного варианта (без walk-to-stop)."""
    return (
        (ta["position"] - a_pos) * stop_time_min
        + (d_pos - tb["position"]) * stop_time_min
        + _transfer_penalty(
            ta,
            tb,
            transfer_penalty_min=transfer_penalty_min,
            transfer_penalty_calc=transfer_penalty_calc,
        )
        + wait_time_min
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
                )
                time_min = _transfer_leg_time_min(
                    a_pos,
                    ta,
                    tb,
                    d_pos,
                    stop_time_min=stop_time_min,
                    wait_time_min=wait_time_min,
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
    wait_calc: str = "linear",
) -> list[_Journey]:
    """Возвращает варианты поездки: (время, ножки), где ножка — (seq_idx, посадка, высадка).

    Включает прямые варианты и (при ``max_transfers > 0``) варианты с одной
    пересадкой между разными маршрутами.

    При передаче ``seq_headway_min``/``seq_jitter_s`` ожидание на пересадке
    считается по расписанию двух линий (координация Takt ``jo``/``hs``):
    две линии «ловит» ту, чей интервал делится на другой; иначе —
    базовое ожидание ``Po(headway)``.
    """
    best_span = _best_direct_spans(origins, destinations)
    journeys = _direct_journeys(
        best_span,
        stop_time_min=stop_time_min,
        walk_to_stop_min=walk_to_stop_min,
        wait_time_min=wait_time_min,
    )

    # Пересадки: остановка маршрута A совпадает (id/радиус) с остановкой
    # маршрута B, причём A проезжает её после посадки, а B — до высадки.
    if max_transfers > 0:
        journeys.extend(
            _transfer_journeys(
                _board_positions(origins),
                _alight_positions(destinations),
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
            )
        )
    return journeys