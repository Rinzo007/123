from __future__ import annotations

import pandas as pd
import pytest

from dedup_policy import dedup_compute_removals


def _analysis(rows: list[tuple[object, object, float]]) -> dict[str, object]:
    pairs = pd.DataFrame(rows, columns=["x", "y", "Kmax"])
    return {
        "ids": [1, 2, 3, 4],
        "pairs": pairs,
        "lengths": {
            1: 10_000.0,
            2: 11_000.0,
            3: 12_000.0,
            4: 13_000.0,
        },
        "meta": {
            1: {"type": "bus", "name": "1"},
            2: {"type": "bus", "name": "2"},
            3: {"type": "bus", "name": "3"},
            4: {"type": "bus", "name": "4"},
        },
    }


def test_sparse_graph_removes_lowest_ghs_without_rebuilding_all_pairs() -> None:
    analysis = _analysis(
        [
            (1, 2, 0.90),
            (2, 3, 0.80),
        ]
    )

    removed, active, residual = dedup_compute_removals(
        analysis,
        k_del=0.70,
        ghs_volumes={1: 100.0, 2: 10.0, 3: 20.0},
    )

    assert [item["маршрут"] for item in removed] == [2]
    assert active == {1, 3, 4}
    assert residual == []
    assert removed[0]["представитель"] == 1


def test_strongest_partner_is_selected_without_full_partner_sort_for_choice() -> None:
    analysis = _analysis(
        [
            (1, 2, 0.80),
            (1, 3, 0.95),
            (1, 4, 0.90),
        ]
    )

    removed, active, residual = dedup_compute_removals(
        analysis,
        k_del=0.70,
        ghs_volumes={1: 1.0, 2: 10.0, 3: 20.0, 4: 30.0},
    )

    assert [item["маршрут"] for item in removed] == [1]
    assert removed[0]["представитель"] == 3
    assert "3 (0.950)" in removed[0]["партнёры (Kmax)"]
    assert active == {2, 3, 4}
    assert residual == []


def test_initial_active_limits_graph_to_previous_pass_survivors() -> None:
    analysis = _analysis(
        [
            (1, 2, 0.90),
            (2, 3, 0.90),
        ]
    )

    removed, active, residual = dedup_compute_removals(
        analysis,
        k_del=0.70,
        ghs_volumes={1: 100.0, 2: 10.0, 3: 20.0},
        initial_active={1, 2, 3},
    )

    assert [item["маршрут"] for item in removed] == [2]
    assert active == {1, 3}
    assert residual == []


def test_stable_direction_keys_work_inside_sparse_policy_graph() -> None:
    analysis = {
        "ids": [(10, 0), (20, 0), (30, 0)],
        "pairs": pd.DataFrame(
            [
                ((10, 0), (20, 0), 0.90),
                ((20, 0), (30, 0), 0.80),
            ],
            columns=["x", "y", "Kmax"],
        ),
        "lengths": {
            (10, 0): 10_000.0,
            (20, 0): 11_000.0,
            (30, 0): 12_000.0,
        },
        "meta": {
            (10, 0): {"type": "bus", "name": "10"},
            (20, 0): {"type": "bus", "name": "20"},
            (30, 0): {"type": "bus", "name": "30"},
        },
    }

    removed, active, residual = dedup_compute_removals(
        analysis,
        k_del=0.70,
        ghs_volumes={(10, 0): 100.0, (20, 0): 10.0, (30, 0): 20.0},
    )

    assert [item["маршрут"] for item in removed] == [(20, 0)]
    assert removed[0]["представитель"] == (10, 0)
    assert active == {(10, 0), (30, 0)}
    assert residual == []


def test_center_weight_requires_distances() -> None:
    analysis = _analysis([((1, 0), (2, 0), 0.90)])
    analysis["ids"] = [(1, 0), (2, 0)]
    analysis["lengths"] = {(1, 0): 10_000.0, (2, 0): 20_000.0}
    analysis["meta"] = {
        (1, 0): {"type": "bus", "name": "1"},
        (2, 0): {"type": "bus", "name": "2"},
    }

    with pytest.raises(ValueError, match="center_distances"):
        dedup_compute_removals(
            analysis,
            k_del=0.70,
            ghs_volumes={(1, 0): 10.0, (2, 0): 20.0},
            center_weight=0.5,
        )
