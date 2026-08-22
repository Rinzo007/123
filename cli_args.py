"""Разбор аргументов командной строки CLI."""

from __future__ import annotations

import argparse
import sys
import warnings

from .constants import MAX_WORKERS


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Экспорт сети общественного транспорта (XLSX + KML)."
    )

    # Основные
    parser.add_argument("city_arg", nargs="?", default=None)
    parser.add_argument("--city", dest="city_flag", default=None)
    parser.add_argument("--url", default=None)
    parser.add_argument("--type", action="append", default=None)
    parser.add_argument(
        "--no-type",
        action="append",
        default=None,
        help="Исключить тип маршрута (можно несколько раз или через запятую, "
        "например --no-type train --no-type water)",
    )
    parser.add_argument("--route", type=str)
    parser.add_argument("--stops", action="store_true")
    parser.add_argument("--format", default="xlsx,kml")
    parser.add_argument("-o", "--output", default=None)
    parser.add_argument(
        "-w",
        "--workers",
        type=int,
        default=MAX_WORKERS,
        help="Количество потоков для загрузки маршрутов",
    )

    # Фильтры
    parser.add_argument(
        "--curv",
        type=float,
        default=2.0,
        help="Макс. коэффициент непрямолинейности (0 = без лимита)",
    )
    parser.add_argument(
        "--minlen",
        type=float,
        default=None,
        help="Мин. длина маршрута, км (0 = без лимита)",
    )
    parser.add_argument(
        "--radius",
        type=float,
        default=None,
        help="Радиус от центра города, км (0 = без лимита)",
    )
    parser.add_argument("--center-lat", type=float, default=None)
    parser.add_argument("--center-lon", type=float, default=None)
    parser.add_argument(
        "--bbox-buffer",
        type=float,
        default=None,
        help="Фиксированный буфер bbox в градусах",
    )
    parser.add_argument("--active-only", action="store_true")
    parser.add_argument(
        "--no-bbox-filter",
        action="store_true",
        help="Не отсекать вторичные маршруты по bbox",
    )
    parser.add_argument(
        "--max-route-number",
        type=int,
        default=0,
        help="Отсеять маршруты без номера, с номером больше указанного "
        "или со скобками в названии (0 = выкл.)",
    )

    # Тепловая карта
    parser.add_argument("--heatmap", action="store_true")
    parser.add_argument("--heat-cell", type=float, default=0.1)
    parser.add_argument("--heat-alpha", default="ff")
    parser.add_argument("--heat-gamma", type=float, default=0.6)
    parser.add_argument("--heat-top", type=float, default=35.0)
    parser.add_argument("--heat-max-height", type=float, default=400.0)
    parser.add_argument("--heat-flat", action="store_true")
    parser.add_argument("--heat-volume", action="store_true")
    parser.add_argument("--heat-vol-radius", type=float, default=800.0)
    parser.add_argument("--heat-smooth", action="store_true")

    # GHS-BUILT-V
    parser.add_argument("--ghs", action="store_true")
    parser.add_argument("--ghs-file", default=None)
    parser.add_argument("--ghs-buffer", type=float, default=800.0)

    # GHS-BUILT-S
    parser.add_argument("--ghs-s", action="store_true")
    parser.add_argument("--ghs-s-file", default=None)
    parser.add_argument("--ghs-s-buffer", type=float, default=800.0)
    parser.add_argument("--ghs-s-max", type=float, default=1e12)

    # POI
    parser.add_argument("--poi", action="store_true")
    parser.add_argument("--poi-buffer", type=float, default=800.0)

    # Дедупликация
    parser.add_argument("--dedup", action="store_true")
    parser.add_argument("--dedup-buffer", type=float, default=100.0)
    parser.add_argument("--dedup-threshold", type=float, default=0.70)
    parser.add_argument(
        "--dedup-passes",
        type=int,
        default=1,
        help="Количество проходов дедупликации (1 = без повторной, 2 = повторная после первой)",
    )
    parser.add_argument(
        "--dedup-center-weight",
        type=float,
        default=0.0,
        help="Учёт расстояния направления до центра маршрутной сети при выборе "
        "удаляемого дубля (0 = выкл., только GHS; 1 = макс. приоритет "
        "периферийных направлений — ядро сети сохраняется)",
    )

    # Генерация маршрутов
    parser.add_argument("--generate", action="store_true")
    parser.add_argument("--gen-count", type=int, default=10)
    parser.add_argument("--gen-population", type=float, default=None)
    parser.add_argument("--gen-transfer", type=float, default=1.15)
    parser.add_argument("--gen-maxlen", type=float, default=20.0)
    parser.add_argument("--gen-corridor", type=float, default=400.0)

    # Идеи пассажиров
    parser.add_argument("--ideas", action="store_true")
    parser.add_argument("--ideas-max-pages", type=int, default=None)
    parser.add_argument("--ideas-search", type=str, default=None)
    parser.add_argument("--ideas-sort", type=str, default=None)

    # Overture Maps
    parser.add_argument("--overture", action="store_true")
    parser.add_argument("--overture-file", default=None)
    parser.add_argument("--overture-buffer", type=float, default=800.0)
    parser.add_argument(
        "--overture-theme",
        default="building",
        help="Тема Overture для автозагрузки: place, building, segment и т.д.",
    )
    parser.add_argument(
        "--overture-release",
        default=None,
        help="Release Overture Maps; по умолчанию OVERTURE_RELEASE или актуальный release.",
    )

    # Пакетный режим
    parser.add_argument(
        "--batch",
        default=None,
        help="Файл с командами (по строке на запуск). Пустые строки, строки "
        "вида '- Заголовок -' и '#' пропускаются.",
    )

    # Кэш
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--cache-dir", default="wikiroutes_cache")

    # Совместимость: старый --dedup-strategy распознаётся, но игнорируется.
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    dedup_strategy = None
    cleaned: list[str] = []
    skip_next = False
    for index, item in enumerate(raw_argv):
        if skip_next:
            skip_next = False
            continue
        if item == "--clean-names":
            warnings.warn(
                "--clean-names больше не применяется: "
                "фильтр по именам убран из базовых типов.",
                DeprecationWarning,
                stacklevel=2,
            )
            continue
        if item == "--dedup-strategy" or item.startswith("--dedup-strategy="):
            warnings.warn(
                "--dedup-strategy больше не используется: "
                "стратегия выбирается автоматически по GHS.",
                DeprecationWarning,
                stacklevel=2,
            )
            if item == "--dedup-strategy":
                if index + 1 < len(raw_argv):
                    dedup_strategy = raw_argv[index + 1]
                skip_next = True
            else:
                dedup_strategy = item.split("=", 1)[1]
            continue
        cleaned.append(item)

    args = parser.parse_args(cleaned)
    args.dedup_strategy = dedup_strategy
    args.clean_names = False
    return args
