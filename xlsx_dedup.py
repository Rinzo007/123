"""Лист XLSX с результатами дедупликации."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .xlsx_helpers import finish_sheet


def write_dedup_sheet(
    wb: Any,
    dedup_removed: Sequence[Mapping[str, Any]],
) -> None:
    """Лист «Удаление дублирования»."""
    ws = wb.create_sheet("Удаление дублирования")
    headers = [
        "Шаг",
        "Направление (ID)",
        "Тип",
        "Название",
        "Закрывает пар",
        "Партнёры (Kmax)",
        "Причина",
        "Длина, км",
    ]
    ws.append(headers)

    for record in dedup_removed:
        ws.append(
            [
                record["шаг"],
                record["маршрут"],
                record["тип"],
                record["название"],
                record["закрывает пар"],
                record["партнёры (Kmax)"],
                record["причина"],
                record["длина, км"],
            ]
        )

    finish_sheet(
        ws,
        headers,
        num_fmt={8: "0.00"},
        wrap={6, 7},
        fixed_widths=[8, 18, 10, 20, 15, 20, 30, 10],
    )
