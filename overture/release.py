"""Разрешение версии набора Overture."""

from __future__ import annotations

from typing import Final
import json
import ssl
import urllib.request

_LATEST_ALIASES: Final[frozenset[str]] = frozenset({"latest", "current"})
_STAC_CATALOG_HOSTS: Final[tuple[str, ...]] = ("https://overturemaps-extras-us-west-2.s3.us-west-2.amazonaws.com/stac", "https://stac.overturemaps.org")
_STAC_TIMEOUT_S = 30.0


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
        resolved = str(resolved).strip()
        if resolved:
            return resolved
    except Exception:
        pass

    errors: list[Exception] = []
    for host in _STAC_CATALOG_HOSTS:
        try:
            request = urllib.request.Request(
                f"{host}/catalog.json",
                headers={"User-Agent": "wikiroutes-overture/1", "Connection": "close"},
            )
            with urllib.request.urlopen(
                request, timeout=_STAC_TIMEOUT_S, context=ssl.create_default_context()
            ) as response:
                catalog = json.loads(response.read())
            for link in catalog.get("links", []):
                if link.get("rel") == "child" and link.get("href"):
                    release_id = str(link["href"]).rstrip("/").rsplit("/", 1)[-1]
                    if release_id:
                        return release_id
        except Exception as exc:
            errors.append(exc)

    detail = f": {errors[-1]}" if errors else ""
    raise OvertureReleaseError(f"Не удалось определить текущий release Overture{detail}")
""Разрешение версии набора Overture."""

from __future__ import annotations

from typing import Final
import json
import ssl
import urllib.request

_LATEST_ALIASES: Final[frozenset[str]] = frozenset({"latest", "current"})
_STAC_CATALOG_HOSTS: Final[tuple[str, ...]] = ("https://overturemaps-extras-us-west-2.s3.us-west-2.amazonaws.com/stac", "https://stac.overturemaps.org")
_STAC_TIMEOUT_S = 30.0


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
