"""Паритет пресетов обобщённой стоимости между model.json и TS-воркером.

Table 5 — действующий стандарт планировщика, Table 8 — именованная
альтернатива из бандла Borough Studio (не проверена по статье, поэтому
только пресет, а не замена). Воркер обязан получать веса инжекцией:
захардкоженных литералов там быть не должно.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from transit_planner.reference_model import (  # noqa: E402
    REFERENCE_JOURNEY_CHOICE,
    REFERENCE_JOURNEY_CHOICE_TABLE8,
)

ROOT = Path(__file__).resolve().parents[1]
ROUTING_TS = ROOT / "frontend" / "src" / "workers" / "routing.ts"
# Логика RAPTOR живёт в ядре, а routing.worker.ts — тонкая обёртка: веса
# проверяются там, где они действительно применяются.
KERNEL_TS = ROOT / "frontend" / "src" / "workers" / "routing-kernel.ts"
WORKER_TS = ROOT / "frontend" / "src" / "workers" / "routing.worker.ts"


def _model() -> dict:
    return json.loads((ROOT / "src" / "transit_planner" / "model.json").read_text(encoding="utf-8"))


def _ts_preset(source: str, name: str) -> dict[str, float]:
    match = re.search(
        rf"{name}:\s*RaptorChoiceWeights\s*=\s*\{{\s*walk:\s*([0-9.]+),\s*wait:\s*([0-9.]+),\s*shift:\s*([0-9.]+)\s*\}}",
        source,
    )
    assert match is not None, f"{name} не найден литералом в routing.ts"
    return {"walk": float(match.group(1)), "wait": float(match.group(2)), "shift": float(match.group(3))}


def test_table8_preset_values() -> None:
    assert (REFERENCE_JOURNEY_CHOICE_TABLE8.walk_weight, REFERENCE_JOURNEY_CHOICE_TABLE8.wait_weight) == (1.39, 1.37)
    assert REFERENCE_JOURNEY_CHOICE_TABLE8.departure_shift_weight == 0.4
    assert REFERENCE_JOURNEY_CHOICE_TABLE8.ride_weight == 1.0


def test_table5_default_unchanged() -> None:
    assert (REFERENCE_JOURNEY_CHOICE.walk_weight, REFERENCE_JOURNEY_CHOICE.wait_weight) == (1.65, 1.72)
    assert REFERENCE_JOURNEY_CHOICE.departure_shift_weight == 0.4


def test_ts_presets_match_model_json() -> None:
    model = _model()
    source = ROUTING_TS.read_text(encoding="utf-8")
    for section, preset in (("journey_choice", "JOURNEY_CHOICE_TABLE5"), ("journey_choice_table8", "JOURNEY_CHOICE_TABLE8")):
        expected = model[section]
        actual = _ts_preset(source, preset)
        assert actual["walk"] == expected["walk_weight"], f"{preset}.walk расходится с model.json"
        assert actual["wait"] == expected["wait_weight"], f"{preset}.wait расходится с model.json"
        assert actual["shift"] == expected["departure_shift_weight"], f"{preset}.shift расходится с model.json"


def test_worker_takes_weights_by_injection() -> None:
    kernel = KERNEL_TS.read_text(encoding="utf-8")
    for literal in ("1.65", "1.72", "1.39", "1.37"):
        assert literal not in kernel, f"ядро содержит захардкоженный вес {literal}"
    assert "input.walkWeight" in kernel
    assert "input.waitWeight" in kernel
    assert "input.shiftWeight" in kernel


def test_worker_wrapper_delegates_to_the_kernel() -> None:
    """Обёртка не должна содержать собственной логики маршрутизации."""
    wrapper = WORKER_TS.read_text(encoding="utf-8")
    assert "routeSingle" in wrapper and "routeAlternatives" in wrapper
    for literal in ("1.65", "1.72", "1.39", "1.37"):
        assert literal not in wrapper, f"обёртка содержит захардкоженный вес {literal}"
