"""Разрешение версии набора Overture."""

from __future__ import annotations

from typing import Final

_LATEST_ALIASES: Final[frozenset[str]] = frozenset({"latest", "current"})


class OvertureReleaseError(RuntimeError):
    """Не удалось определить конкретный release Overture."""


def resolve_overture_release(release: str | None = None) -> str:
    """Возвращает конкретный release; alias latest/current никогда не кешируется."""
    value = release.strip() if isinstance(release, str) else release
    if value and value.lower() not in _LATEST_ALIASES:
        return value
    try:
        import overturemaps

        resolved = overturemaps.core.get_latest_release()
    except Exception as exc:  # noqa: BLE001 — внешняя зависимость
        raise OvertureReleaseError(
            "Не удалось определить текущий release Overture"
        ) from exc

    resolved = str(resolved).strip()
    if not resolved:
        raise OvertureReleaseError("Overture вернул пустой release")
    return resolved
