"""
CLI для экспорта сети общественного транспорта.

Запуск:

python -m wikiroutes kiev
python -m wikiroutes kiev --overture --overture-theme building
python -m wikiroutes kiev --ghs --ghs-file ./ghs.tif --poi --dedup
"""

from __future__ import annotations

import contextlib
import re
import shlex
import sys
import traceback
from pathlib import Path

from .cache import JsonCache
from .cli_args import parse_args as parse_args
from .config import CliConfig, CliConfigError, build_cli_config
from .errors import CatalogLoadError
from .heatmap import build_heatmap_kml
from .http_client import SessionProvider
from .kml import build_kml
from .models import RouteData
from .pipeline import (
    BASE_ROUTE_TYPES,
    PipelineContext,
    PipelineResult,
    run_pipeline,
)
from .report import Reporter, render_errors
from .xlsx import build_xlsx

__all__ = [
    "BASE_ROUTE_TYPES",
    "main",
    "parse_args",
    "run_batch",
]

# Совместимость: BASE_ROUTE_TYPES переехал в pipeline, но остаётся
# доступным отсюда (используется тестами и внешним кодом).


# ═══════════════════════════════════════════════════════════════════════
# ПАКЕТНЫЙ РЕЖИМ
# ═══════════════════════════════════════════════════════════════════════


def _parse_batch_file(
    batch_file: str,
) -> list[tuple[str, list[str]]]:
    """Читает файл команд и возвращает список ``(сырая строка, аргументы)``.

    Пустые строки, строки вида ``- Заголовок -`` и ``#``-комментарии
    пропускаются. Поддерживается префикс ``python -m wikiroutes`` / ``wikiroutes``.
    Точные дубликаты команд пропускаются. ``--batch`` внутри файла игнорируется
    (защита от рекурсии).
    """
    try:
        with Path(batch_file).open(encoding="utf-8-sig") as fh:
            text = fh.read()
    except UnicodeDecodeError:
        with Path(batch_file).open(encoding="cp1251") as fh:
            text = fh.read()

    commands: list[tuple[str, list[str]]] = []
    seen: set[tuple[str, ...]] = set()

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "-")):
            continue
        if " #" in stripped:
            stripped = stripped.split(" #", 1)[0].strip()
        tokens = shlex.split(stripped)
        if not tokens:
            continue
        if tokens[0] in ("python", "py", "python3") and tokens[1:3] == [
            "-m",
            "wikiroutes",
        ]:
            tokens = tokens[3:]
        elif tokens[0] == "wikiroutes":
            tokens = tokens[1:]

        # защита от рекурсии: --batch внутри файла игнорируется
        cleaned: list[str] = []
        skip_next = False
        for _index, item in enumerate(tokens):
            if skip_next:
                skip_next = False
                continue
            if item == "--batch":
                skip_next = True
                continue
            if item.startswith("--batch="):
                continue
            cleaned.append(item)
        if not cleaned:
            continue

        key = tuple(cleaned)
        if key in seen:
            print(f"  ⚠ дубль команды пропущен: {' '.join(cleaned)}")
            continue
        seen.add(key)
        commands.append((stripped, cleaned))

    return commands


def run_batch(batch_file: str) -> int:
    """Выполняет команды из файла последовательно в одном процессе.

    Ошибка одной команды не прерывает пакет; в конце выводится сводка.
    """
    # В пакетном режиме выводятся символы вне cp1251 (⚠, ✅, →) — включаем UTF-8,
    # чтобы отдельная команда не падала на UnicodeEncodeError.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(ValueError):
                reconfigure(encoding="utf-8", errors="replace")

    commands = _parse_batch_file(batch_file)

    if not commands:
        print(f"⚠ В файле {batch_file} не найдено ни одной команды.")
        return 1

    print(f"Пакет: {len(commands)} команд из {batch_file}\n")
    failures: list[str] = []

    for index, (raw_line, tokens) in enumerate(commands, start=1):
        city = tokens[0] if tokens and not tokens[0].startswith("-") else ""
        label = (
            f"[{index}/{len(commands)}] {city} "
            if city
            else f"[{index}/{len(commands)}]"
        )
        print(f"{label}→ {' '.join(tokens)}")
        try:
            code = main(tokens)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
        except Exception:
            traceback.print_exc()
            code = 1
        if code:
            failures.append(f"  ✗ {raw_line} (код {code})")

    print("\n" + "═" * 60)
    print(f"Готово: {len(commands) - len(failures)}/{len(commands)} команд выполнено.")
    if failures:
        print("Неудачные команды:")
        print("\n".join(failures))
    print("═" * 60)

    return 1 if failures else 0


# ═══════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════


def _output_base_name(config: CliConfig, city_slug: str) -> str:
    """Базовое имя выходных файлов без расширения."""
    safe_city_name = (
        re.sub(r"[^a-z0-9_-]+", "_", city_slug.lower()).strip("_") or "city"
    )

    output_base = config.output or f"{safe_city_name}_routes"
    return re.sub(r"\.(xlsx|kml)$", "", output_base, flags=re.IGNORECASE)


def _write_xlsx(
    config: CliConfig,
    result: PipelineResult,
    kml_routes: list[RouteData],
    output_base: str,
) -> None:
    """Собирает полный XLSX-отчёт и печатает путь."""
    limits = result.limits
    excluded_counts = result.excluded_counts

    xlsx_path = build_xlsx(
        result.all_routes,  # позиционно
        result.city_slug,  # позиционно
        result.city_title,  # позиционно
        output_base + ".xlsx",  # позиционно
        curv_limit=limits.curvilinearity,
        minlen_limit=limits.min_length_km,
        radius_limit=limits.radius_km,
        cut_curv=excluded_counts.get("криволинейность", 0),
        cut_len=excluded_counts.get("длина", 0),
        cut_radius=excluded_counts.get("радиус", 0),
        skipped_inactive=result.skipped_inactive,
        kml_routes=kml_routes,
        unique_stops=result.unique_stops,
        excluded_stage2=result.excluded_routes,
        heatmap=result.heatmap,
        include_stops=config.stops,
        bbox=result.bbox,
        ghs_stats=result.ghs_stats,
        ghs_meta=result.ghs_meta,
        ghs_dir_stats=result.ghs_dir_stats,
        poi_stats=result.poi_stats,
        poi_values=result.poi_values,
        poi_dir_stats=result.poi_dir_stats,
        poi_buffer_m=config.poi_buffer if config.poi else 0,
        dedup_removed=result.dedup_removed,
        dedup_analysis=result.dedup_analysis,
        net_metrics=result.net_metrics,
        generated_routes=result.generated_routes,
        built_s_stats=result.built_s_stats,
        built_s_meta=result.built_s_meta,
        built_s_dir_stats=result.built_s_dir_stats,
        overture_stats=result.overture_stats,
        overture_meta=result.overture_meta,
        overture_dir_stats=result.overture_dir_stats,
        stop_volumes=result.stop_volumes,
        vol_radius=config.heat_vol_radius,
    )

    if xlsx_path:
        print(f"  ✅ XLSX : {xlsx_path}")


def _write_kml(
    config: CliConfig,
    result: PipelineResult,
    kml_routes: list[RouteData],
    output_base: str,
) -> None:
    """Собирает KML отфильтрованных маршрутов и печатает путь."""
    kml_path = build_kml(
        kml_routes,
        result.city_title,
        output_base + ".kml",
        include_stops=config.stops,
        bbox=result.bbox,
        ghs_stats=result.ghs_stats,
        ghs_buffer=config.ghs_buffer if config.ghs else 0,
        poi_stats=result.poi_stats,
        poi_buffer=config.poi_buffer if config.poi else 0,
        generated=result.generated_routes,
        built_s_stats=result.built_s_stats,
        built_s_meta=result.built_s_meta,
        overture_stats=result.overture_stats,
        overture_meta=result.overture_meta,
        overture_dir_stats=result.overture_dir_stats,
    )

    if kml_path:
        print(f"  ✅ KML  : {kml_path}  (маршрутов: {len(kml_routes)})")


def _write_heatmap(result: PipelineResult, config: CliConfig, output_base: str) -> None:
    """Печатает heatmap-KML, если тепловая карта рассчитана."""
    if not result.heatmap:
        return

    heatmap_kml_path = build_heatmap_kml(
        result.heatmap,
        result.city_title,
        output_base + "_heatmap.kml",
        max_height=config.heat_max_height,
        flat=config.heat_flat,
    )

    if heatmap_kml_path:
        print(
            f"  ✅ KML heatmap: {heatmap_kml_path} "
            f"(ячеек: {len(result.heatmap['cells'])})"
        )


def _export_outputs(config: CliConfig, result: PipelineResult) -> None:
    """Стадия [4/4]: генерация выходных файлов и отчёт об ошибках загрузки."""
    print("\n[4/4] Генерация выводов...")

    kml_routes = result.ok_routes
    output_base = _output_base_name(config, result.city_slug)

    if "xlsx" in config.output_formats:
        _write_xlsx(config, result, kml_routes, output_base)

    if "kml" in config.output_formats:
        _write_kml(config, result, kml_routes, output_base)

    _write_heatmap(result, config, output_base)

    errors_text = render_errors(result.bad_routes)

    if errors_text:
        print(f"\n{errors_text}")


def main(argv: list[str] | None = None) -> int:
    # В одиночном режиме тоже выводятся символы вне cp1251 (³, →, ⚠) —
    # включаем UTF-8, чтобы не падать на UnicodeEncodeError в консоли Windows.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(ValueError):
                reconfigure(encoding="utf-8", errors="replace")

    args = parse_args(argv)

    if args.batch:
        return run_batch(args.batch)

    try:
        config = build_cli_config(args)
    except CliConfigError as exc:
        sys.exit(str(exc))

    read_cache = not args.no_cache and not args.refresh
    write_cache = not args.no_cache

    cache = JsonCache(config.cache_dir, read=read_cache, write=write_cache)

    print("=" * 65)
    print("  Экспорт сети: XLSX (полный) + KML (все отфильтрованные)")
    print("  Источник: ru.wikiroutes.info")
    print("=" * 65)

    print(
        f"\nГород: {config.city_input}\n"
        f"Каталог: {config.catalog_url}\n"
        f"Форматы: {', '.join(sorted(config.output_formats))}"
    )

    print("\n[1/4] Загрузка каталога...")

    with SessionProvider() as sessions:
        reporter = Reporter(echo=True)

        try:
            result = run_pipeline(
                config,
                PipelineContext(cache=cache, sessions=sessions, reporter=reporter),
            )
        except CatalogLoadError as exc:
            sys.exit(f"ОШИБКА загрузки каталога: {exc}")
        except (CliConfigError, ValueError) as exc:
            sys.exit(str(exc))

        _export_outputs(config, result)

    print("\nГотово.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
