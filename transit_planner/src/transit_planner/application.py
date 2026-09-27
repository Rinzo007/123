"""Application service boundary for Transit Planner.

API handlers should orchestrate through this layer instead of embedding domain
calculations. It deliberately contains no construction lifecycle or CAPEX.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, TypeVar

from .network import Network

T = TypeVar("T")


@dataclass(slots=True)
class ComputeResult:
    value: Any
    cache_key: str | None = None


class TransitPlannerApplication:
    """Coordinates the canonical network and calculation services."""

    def __init__(self, network: Network | None = None) -> None:
        self.network = network or Network()
        self._cache: dict[str, Any] = {}

    def invalidate(self) -> None:
        self._cache.clear()

    def cached(self, key: str, factory: Callable[[], T]) -> T:
        if key in self._cache:
            return self._cache[key]
        value = factory()
        self._cache[key] = value
        return value

    def replace_network(self, network: Network) -> None:
        self.network = network
        self.invalidate()
