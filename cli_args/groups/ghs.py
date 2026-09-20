"""Аргументы застройки GHS-BUILT-V и GHS-BUILT-S."""
from __future__ import annotations

import argparse

from ...config import DEFAULT_GHS_FILE


def add_ghs_args(parser: argparse.ArgumentParser) -> None:
    """Показатели застройки GHS-BUILT-V / GHS-BUILT-S."""
    # GHS-BUILT-V
    parser.add_argument(
        "--ghs",
        action="store_true",
        help="Рассчитать показатели застройки GHS-BUILT-V.",
    )
    parser.add_argument(
        "--ghs-file",
        default=None,
        help=(
            "Файл данных GHS-BUILT-V; без файла используется растр населения "
            "страны выбранного города из D:\\Programs\\Cities2\\GHS "
            f"(для неизвестных городов — стандартный источник: {DEFAULT_GHS_FILE})."
        ),
    )
    parser.add_argument(
        "--ghs-buffer",
        type=float,
        default=600.0,
        help="Радиус буфера GHS-BUILT-V вокруг маршрута в метрах.",
    )

    # GHS-BUILT-S
    parser.add_argument(
        "--ghs-s",
        action="store_true",
        help="Рассчитать показатели застройки GHS-BUILT-S.",
    )
    parser.add_argument(
        "--ghs-s-file",
        default=None,
        help="Файл данных GHS-BUILT-S.",
    )
    parser.add_argument(
        "--ghs-s-buffer",
        type=float,
        default=None,
        help="Радиус буфера GHS-BUILT-S в метрах; по умолчанию берётся из --ghs-buffer.",
    )
    parser.add_argument(
        "--ghs-s-max",
        type=float,
        default=1e12,
        help="Максимально учитываемая площадь или объём GHS-BUILT-S.",
    )
