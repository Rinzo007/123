"""Аргументы CLI для экспорта Takt city pack (пассажиропоток-оркестратор)."""
from __future__ import annotations

import argparse

from ...config import DEFAULT_DATA_DIR


def add_pack_args(parser: argparse.ArgumentParser) -> None:
    """Сборка Takt city pack из OD-артефактов и сети маршрутов."""
    parser.add_argument(
        "--pack",
        action="store_true",
        help="Собрать Takt city pack для оркестратора пассажиропотока "
        "(demand.json, districts.json, baseline.json, model.json, purposes.bin.json, demand-streets.json, manifest.json).",
    )
    parser.add_argument(
        "--pack-out",
        dest="pack_out",
        default=None,
        help="Каталог для файлов pack; по умолчанию текущий каталог.",
    )
    parser.add_argument(
        "--pack-data",
        dest="pack_data",
        default=None,
        help="Каталог с OD-артефактами <city>_od_*.parquet/.geojson; "
        f"по умолчанию {DEFAULT_DATA_DIR}.",
    )
    parser.add_argument(
        "--pack-version",
        dest="pack_version",
        type=int,
        default=1,
        help="Версия manifest pack (по умолчанию 1).",
    )
    parser.add_argument(
        "--no-pack-report",
        dest="pack_report",
        action="store_false",
        help="Не запускать оркестратор пассажиропотока после сборки pack.",
    )
    parser.add_argument(
        "--pack-reports",
        dest="pack_reports",
        default=None,
        help="Каталог для отчётов оркестратора; по умолчанию <pack-out>/reports.",
    )
    parser.add_argument(
        "--pack-orchestrator",
        dest="pack_orchestrator",
        default=None,
        help="Каталог пакета passengerflow оркестратора; "
        "по умолчанию WIKIROUTES_ORCHESTRATOR_DIR.",
    )
    parser.add_argument(
        "--pack-max-pairs",
        dest="pack_max_pairs",
        type=int,
        default=25,
        help="Число топ-OD-пар в отчёте оркестратора (по умолчанию 25).",
    )
    parser.add_argument(
        "--takt-model",
        dest="takt_model",
        action="store_true",
        help="Запустить takt-model simulation после сборки pack.",
    )
    parser.add_argument(
        "--takt-model-out",
        dest="takt_model_out",
        default=None,
        help="Путь к JSON-отчёту takt-model (по умолчанию <pack-out>/takt_model.json).",
    )