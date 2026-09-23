from pathlib import Path
import sys

# Исходный пакет приложения теперь находится в wikiroutes/.
PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "wikiroutes"
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

"""Минимальные host-stubs для тестирования Overture как изолированного компонента."""

from dataclasses import dataclass
import types


adapters = types.ModuleType("overture.adapters")


@dataclass(frozen=True)
class OvertureStats:
    total_area_m2: float = 0.0
    corridor_m2: float = 0.0
    count: int = 0
    ok: bool = True


class JsonCache:
    def __init__(self):
        self.data = {}

    def get(self, namespace, key):
        return self.data.get((namespace, key))

    def put(self, namespace, key, value):
        self.data[(namespace, key)] = value


def dir_geo_sig(direction):
    return getattr(direction, "signature", repr(direction))


def resolve_sources(path, extensions):
    return [path] if path else []


def utm_epsg(*_args, **_kwargs):
    return 32631


adapters.OvertureStats = OvertureStats
adapters.JsonCache = JsonCache
adapters.dir_geo_sig = dir_geo_sig
adapters.resolve_sources = resolve_sources
adapters.utm_epsg = utm_epsg
sys.modules.setdefault("overture.adapters", adapters)


context = types.ModuleType("overture.context")


class _OvertureContext:
    pass


context._OvertureContext = _OvertureContext
sys.modules.setdefault("overture.context", context)
