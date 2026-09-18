from types import SimpleNamespace

import pytest

from overture.release import OvertureReleaseError, resolve_overture_release


def test_explicit_release_does_not_touch_overturemaps():
    assert resolve_overture_release("2025-01-15") == "2025-01-15"


def test_latest_release_is_resolved(monkeypatch):
    fake_core = SimpleNamespace(get_latest_release=lambda: "2026-09-01")
    fake_module = SimpleNamespace(core=fake_core)
    monkeypatch.setitem(__import__("sys").modules, "overturemaps", fake_module)
    assert resolve_overture_release("latest") == "2026-09-01"
    assert resolve_overture_release("current") == "2026-09-01"


def test_latest_release_failure_is_explicit(monkeypatch):
    def boom():
        raise RuntimeError("network")

    fake_core = SimpleNamespace(get_latest_release=boom)
    monkeypatch.setitem(
        __import__("sys").modules,
        "overturemaps",
        SimpleNamespace(core=fake_core),
    )
    with pytest.raises(OvertureReleaseError):
        resolve_overture_release("latest")
