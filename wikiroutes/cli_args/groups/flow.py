"""Аргументы расчёта пассажиропотока."""
from __future__ import annotations

import argparse


def add_flow_args(parser: argparse.ArgumentParser) -> None:
    """Включает полный расчёт пассажиропотока.

    Параметры модели (``flow_*``) утратили CLI-флаги и задаются программно
    через поля ``CliConfig`` или файлом ``takt_model``.
    """
    parser.add_argument(
        "--passenger-flow",
        dest="passenger_flow",
        action="store_true",
        help="Полный расчёт пассажиропотока: генерирует OD-матрицу и распределяет "
        "поездки на маршруты через логит-модель.",
    )