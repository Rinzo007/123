"""Регресс-тест на отсоединение переданных массивов.

В `buildReferenceDemand` входные типизированные массивы передаются воркеру с
`transfer`, после чего на главном потоке они отсоединены и чтение даёт
`undefined`, а из `undefined` — `NaN`. Именно так в браузере зоны приходили с
NaN-координатами, и все метрики assignment становились нечисловыми.

Node-тесты этого не видят: там нет настоящего `Worker` и отсоединения. Поэтому
защита статическая: обработчик ответа не должен читать переданные массивы.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEMAND_TS = ROOT / "frontend" / "src" / "workers" / "demand.ts"

# Массивы, которые уходят в воркер по transfer.
TRANSFERRED = (
    "input.zones.x",
    "input.zones.y",
    "input.zones.population",
    "input.zones.jobs",
    "input.zones.attractions",
    "input.places.lon",
    "input.places.lat",
    "input.places.importance",
    "input.purposes.tripsPerResource",
    "input.purposes.attractionDistanceM",
    "input.purposes.maxDestinations",
    "input.purposes.outboundShares",
    "input.purposes.returnShares",
    "input.periods.startMinute",
    "input.periods.endMinute",
    "input.periods.outboundShare",
    "input.periods.returnShare",
)


def _onmessage_body(source: str) -> str:
    """Текст обработчика ответа воркера."""
    start = source.index("worker.onmessage")
    # Обработчик заканчивается на postMessage того же сообщения.
    end = source.index("worker.postMessage", start)
    return source[start:end]


def test_demand_wrapper_has_a_handler() -> None:
    source = DEMAND_TS.read_text(encoding="utf-8")
    assert "worker.onmessage" in source
    assert "worker.postMessage" in source


def test_response_handler_does_not_read_transferred_arrays() -> None:
    """Главный поток не читает отсоединённые массивы."""
    handler = _onmessage_body(DEMAND_TS.read_text(encoding="utf-8"))
    for name in TRANSFERRED:
        assert name not in handler, (
            f"обработчик читает {name}, но этот массив передан воркеру с transfer "
            "и на главном потоке отсоединён — значение будет undefined"
        )


def test_zone_columns_are_built_before_posting() -> None:
    """Колонки зон считаются до отправки, а не в обработчике ответа."""
    source = DEMAND_TS.read_text(encoding="utf-8")
    builder = source.index("function zoneColumnsFromInput")
    post = source.index("worker.postMessage")
    call = source.index("zoneColumnsFromInput(input)", builder)
    assert builder < post, "функция сборки колонок объявлена после отправки"
    assert call < post, "колонки зон должны собираться до postMessage"


def test_transferred_arrays_are_still_listed() -> None:
    """Список переноса остаётся полным: тест выше опирается именно на него."""
    source = DEMAND_TS.read_text(encoding="utf-8")
    transfer_block = source[source.index("transfer: ["):]
    transfer_block = transfer_block[:transfer_block.index("]")]
    listed = set(re.findall(r"input\.([a-zA-Z.]+)\.buffer", transfer_block))
    expected = {name.split("input.")[1] for name in TRANSFERRED}
    assert expected <= listed, f"не передаются: {sorted(expected - listed)}"
