"""Пакетный режим CLI.

Вынесен из основного CLI-runtime, чтобы пакетное выполнение команд не
смешивалось с интерактивным запуском и экспортом результатов.
"""

from __future__ import annotations

import contextlib
import shlex
import sys
import traceback
from pathlib import Path
from typing import Callable


def parse_batch_file(batch_file: str) -> list[tuple[str, list[str]]]:
    """Читает файл команд и возвращает ``(сырая строка, аргументы)``."""
    try:
        text = Path(batch_file).read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = Path(batch_file).read_text(encoding="cp1251")

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

        cleaned: list[str] = []
        skip_next = False
        for item in tokens:
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


def run_batch(batch_file: str, main: Callable[[list[str]], int]) -> int:
    """Выполняет команды последовательно в одном процессе."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(ValueError):
                reconfigure(encoding="utf-8", errors="replace")

    commands = parse_batch_file(batch_file)
    if not commands:
        print(f"⚠ В файле {batch_file} не найдено ни одной команды.")
        return 1

    print(f"Пакет: {len(commands)} команд из {batch_file}\n")
    failures: list[str] = []

    for index, (raw_line, tokens) in enumerate(commands, start=1):
        city = tokens[0] if tokens and not tokens[0].startswith("-") else ""
        label = f"[{index}/{len(commands)}] {city} " if city else f"[{index}/{len(commands)}]"
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


__all__ = ["parse_batch_file", "run_batch"]
