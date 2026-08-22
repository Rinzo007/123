"""Политики удаления маршрутов после spatial/stop analysis.

Этот модуль отделяет decision policy от геометрического анализа в `dedup.py`.

Для выбора жертвы обязательно используется GHS.
Удаление начинается с направления с самым маленьким объёмом застройки GHS.
"""

from __future__ import annotations

import contextlib
from typing import Any

import numpy as np


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Преобразует значение в конечный float или возвращает `default`."""
    try:
        result = float(value)
    except TypeError, ValueError:
        return default

    return result if np.isfinite(result) else default


def dedup_compute_removals(
    analysis: dict[str, Any],
    k_del: float,
    ghs_volumes: dict[int, float],
    ghs_weight: float = 1.0,
    initial_active: set[int] | None = None,
    center_distances: dict[int, float] | None = None,
    center_weight: float = 0.0,
) -> tuple[list[dict[str, Any]], set[int], list[dict[str, Any]]]:
    """Удаляет дублирующие направления, начиная с минимального объёма GHS.

    Порядок выбора:

    * рассматриваются только активные пары с ``Kmax > k_del``;
    * из всех участников таких пар выбирается направление с минимальным GHS;
    * при ``center_weight > 0`` GHS нормализуется к диапазону [0, 1] и
      смешивается с нормированным расстоянием направления до центра сети:
      ``score = (1 - w) * GHS_н - w * расстояние_н`` — чем больше вес, тем
      сильнее приоритет удаления направлений, удалённых от центра
      (ядро маршрутной сети сохраняется);
    * при равенстве score выбирается направление с более сильной парой;
    * при равенстве Kmax выбирается направление с меньшим ID;
    * после каждого удаления активный набор направлений меняется;
    * следующая жертва выбирается заново.

    ``initial_active`` позволяет начать с набора, переданного вызывающим кодом
    (например, выживших направлений предыдущего прохода), а не со всех ``ids``.
    """
    if ghs_volumes is None or not ghs_volumes:
        raise ValueError(
            "ghs_volumes обязателен: дедупликация без GHS не поддерживается"
        )

    ids: list[int] = []

    for item in analysis.get("ids", []):
        try:
            ids.append(int(item))
        except TypeError, ValueError, OverflowError:
            continue

    active = set(ids)

    if initial_active is not None:
        active &= set(initial_active)

    base_pairs = analysis.get("pairs")

    if base_pairs is None or getattr(base_pairs, "empty", True):
        return [], active, []

    if "Kmax" not in getattr(base_pairs, "columns", []):
        return [], active, []

    k_del = _safe_float(k_del, default=float("nan"))

    if not np.isfinite(k_del):
        return [], active, []

    if not 0.0 <= k_del <= 1.0:
        raise ValueError("k_del должен быть числом в диапазоне [0, 1]")

    ghs_weight = max(0.0, _safe_float(ghs_weight))

    if ghs_weight <= 0.0:
        raise ValueError(
            "ghs_weight должен быть положительным: GHS обязателен для дедупликации"
        )

    center_dist = center_distances or {}
    center_weight = min(1.0, max(0.0, _safe_float(center_weight)))

    if center_weight > 0.0 and not center_dist:
        raise ValueError(
            "center_weight > 0 требует center_distances "
            "(расстояния направлений до центра маршрутной сети)"
        )

    ghs = ghs_volumes
    lengths = analysis.get("lengths", {})
    meta = analysis.get("meta", {})

    def _ghs_value(route_id: int) -> float:
        """Возвращает неотрицательный объём GHS для направления."""
        return max(0.0, _safe_float(ghs.get(route_id)))

    def _dist_value(route_id: int) -> float:
        """Возвращает расстояние направления до центра сети (км, >= 0)."""
        return max(0.0, _safe_float(center_dist.get(route_id)))

    # Инкрементальные структуры: вместо actualize_pairs() с полной
    # перефильтрацией и пересортировкой O(P·log P) на каждое удаление
    # отбираем пары один раз и на каждом шаге вычёркиваем только пары жертвы.
    partners_by_route: dict[int, list[tuple[int, float]]] = {}
    max_k_by_route: dict[int, float] = {}

    has_k2 = "K2_xy" in base_pairs.columns and "K2_yx" in base_pairs.columns

    pair_cols = ["x", "y", "K2_xy", "K2_yx"] if has_k2 else ["x", "y", "Kmax"]

    seen_pairs: set[tuple[int, int]] = set()

    for row in base_pairs[pair_cols].itertuples(index=False, name=None):
        if has_k2:
            x, y, k2_xy, k2_yx = row
        else:
            x, y, k2_xy = row
            k2_yx = k2_xy

        try:
            x_id = int(x)
            y_id = int(y)
        except TypeError, ValueError, OverflowError:
            continue

        if x_id == y_id:
            continue

        if x_id not in active or y_id not in active:
            continue

        pair_key = (x_id, y_id) if x_id < y_id else (y_id, x_id)

        if pair_key in seen_pairs:
            continue

        seen_pairs.add(pair_key)

        k_value = _safe_float(np.nanmax((k2_xy, k2_yx)))

        if k_value > k_del:
            partners_by_route.setdefault(x_id, []).append((y_id, k_value))
            partners_by_route.setdefault(y_id, []).append((x_id, k_value))

            if k_value > max_k_by_route.get(x_id, 0.0):
                max_k_by_route[x_id] = k_value

            if k_value > max_k_by_route.get(y_id, 0.0):
                max_k_by_route[y_id] = k_value

    removed: list[dict[str, Any]] = []

    while partners_by_route:
        # Нормализация по текущему набору кандидатов: веса смешивают GHS
        # и расстояние до центра сети в общем диапазоне [0, 1].
        ghs_max = max((_ghs_value(rid) for rid in partners_by_route), default=0.0)
        dist_max = max((_dist_value(rid) for rid in partners_by_route), default=0.0)

        def _score(route_id: int, ghs_max: float, dist_max: float) -> float:
            ghs_n = _ghs_value(route_id) / ghs_max if ghs_max > 0.0 else 0.0
            dist_n = _dist_value(route_id) / dist_max if dist_max > 0.0 else 0.0
            return (1.0 - center_weight) * ghs_n - center_weight * dist_n

        # Начинаем с направления с самым маленьким объёмом застройки GHS
        # (при center_weight > 0 — смешанным с расстоянием до центра сети).
        victim = min(
            partners_by_route,
            key=lambda route_id: (
                _score(route_id, ghs_max, dist_max),
                -max_k_by_route.get(route_id, 0.0),
                route_id,
            ),
        )

        partners = partners_by_route.pop(victim)
        max_k_by_route.pop(victim, None)

        partners.sort(key=lambda item: (-item[1], item[0]))

        if not partners:
            continue

        # После каждого удаления активный набор направлений меняется.
        active.discard(victim)

        main_partner_id, main_partner_k = partners[0]

        dist_km = _dist_value(victim)

        reason = (
            f"удаляем маршрут с минимальным объёмом застройки "
            f"при K={main_partner_k:.3f}; "
            f"GHS={_ghs_value(victim) / 1e3:.1f} тыс. м³"
        )

        if center_weight > 0.0:
            reason += f"; расстояние до центра сети: {dist_km:.2f} км"

        removed.append(
            {
                "шаг": len(removed) + 1,
                "маршрут": victim,
                "представитель": main_partner_id,
                "тип": meta.get(victim, {}).get("type", ""),
                "название": meta.get(victim, {}).get("name", ""),
                "закрывает пар": len(partners),
                "партнёры (Kmax)": ", ".join(
                    f"{partner_id} ({kmax:.3f})" for partner_id, kmax in partners
                ),
                "причина": reason,
                "длина, км": round(_safe_float(lengths.get(victim)) / 1000.0, 2),
                "застройка GHS, тыс. м³": round(
                    _ghs_value(victim) / 1000.0,
                    1,
                ),
                "расстояние до центра, км": round(dist_km, 2),
            }
        )

        # Инкрементально вычёркиваем пары жертвы из структур партнёров.
        for partner_id, k_value in partners:
            partner_pairs = partners_by_route.get(partner_id)

            if partner_pairs is None:
                continue

            with contextlib.suppress(ValueError):
                partner_pairs.remove((victim, k_value))

            if k_value == max_k_by_route.get(partner_id):
                max_k_by_route[partner_id] = max(
                    (k for _, k in partner_pairs),
                    default=0.0,
                )

            if not partner_pairs:
                del partners_by_route[partner_id]
                max_k_by_route.pop(partner_id, None)

    residual: list[dict[str, Any]] = []

    active_pairs = base_pairs[
        base_pairs["x"].isin(active) & base_pairs["y"].isin(active)
    ]

    for row in active_pairs.itertuples():
        try:
            x_id = int(row.x)
            y_id = int(row.y)
        except TypeError, ValueError, OverflowError, AttributeError:
            continue

        if x_id == y_id:
            continue

        kmax = _safe_float(row.Kmax)

        if kmax > k_del:
            residual.append(
                {
                    "x": x_id,
                    "y": y_id,
                    "Kmax": kmax,
                }
            )

    return removed, active, residual
