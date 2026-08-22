"""Потокобезопасный файловый JSON-кэш с ключами на основе SHA-256."""

import contextlib
import hashlib
import json
import logging
import threading
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger("wikiroutes.cache")

DEFAULT_MAX_ENTRY_BYTES = 8 * 1024 * 1024


class JsonCache:
    """Потокобезопасный файловый JSON-кэш с ключами на основе SHA-256.

    Запись выполняется атомарно (уникальный временный файл + ``os.replace``),
    поэтому конкурентное чтение никогда не видит частично записанные данные.
    При одновременной записи одного ключа выигрывает последний полный дамп.
    Размер отдельной записи ограничен ``max_entry_bytes``.
    """

    def __init__(
        self,
        directory: Path | str = "wikiroutes_cache",
        *,
        read: bool = True,
        write: bool = True,
        max_entry_bytes: int = DEFAULT_MAX_ENTRY_BYTES,
    ) -> None:
        if max_entry_bytes <= 0:
            raise ValueError("max_entry_bytes должен быть положительным")

        self.root = Path(directory)
        self.read_enabled = read
        self.write_enabled = write
        self.max_entry_bytes = max_entry_bytes
        self._lock = threading.Lock()
        self._made_dirs: set[Path] = set()

    @staticmethod
    def _validate_kind(kind: str) -> None:
        if (
            not kind
            or kind in {".", ".."}
            or "/" in kind
            or "\\" in kind
            or kind != Path(kind).name
        ):
            raise ValueError(f"JsonCache: недопустимый kind: {kind!r}")

    def _path(self, kind: str, key: str) -> Path:
        self._validate_kind(kind)
        digest = hashlib.sha256(f"{kind}:{key}".encode()).hexdigest()
        return self.root / kind / f"{digest}.json"

    def _ensure_parent(self, path: Path) -> None:
        parent = path.parent
        if parent in self._made_dirs:
            return
        with self._lock:
            if parent in self._made_dirs:
                return
            parent.mkdir(parents=True, exist_ok=True)
            self._made_dirs.add(parent)

    def get(self, kind: str, key: str) -> Any | None:
        if not self.read_enabled:
            return None

        path = self._path(kind, key)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError) as exc:
            logger.debug("Cache read failed for %s/%s: %s", kind, key, exc)
            return None

    def put(self, kind: str, key: str, obj: Any) -> None:
        if not self.write_enabled:
            return

        path = self._path(kind, key)
        self._ensure_parent(path)
        tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")

        try:
            # Компактный JSON (без отступов) — меньше байт и быстрее сериализация.
            payload = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
            payload_size = len(payload.encode("utf-8"))

            if payload_size > self.max_entry_bytes:
                logger.warning(
                    "Cache entry %s/%s skipped: %d bytes exceeds limit %d",
                    kind,
                    key,
                    payload_size,
                    self.max_entry_bytes,
                )
                return

            # Глобальная блокировка не нужна: ``os.replace`` атомарен,
            # читатели увидят либо старую, либо новую запись целиком.
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(path)
        except OSError, TypeError, ValueError:
            logger.exception("Cache write failed for %s/%s", kind, key)
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)
