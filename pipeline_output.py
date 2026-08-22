"""Функции финального output/generation-слоя pipeline."""

from __future__ import annotations

from typing import Any

from .generate import (
    _stops_area_km2 as _stops_area_km2_impl,
    compute_stop_volumes as _compute_stop_volumes_impl,
    gen_route_count_formula as _gen_route_count_formula_impl,
    generate_routes_network as _generate_routes_network_impl,
)
from .heatmap import build_heatmap as _build_heatmap_impl


def stops_area_km2(*args: Any, **kwargs: Any) -> Any:
    return _stops_area_km2_impl(*args, **kwargs)


def compute_stop_volumes(*args: Any, **kwargs: Any) -> Any:
    return _compute_stop_volumes_impl(*args, **kwargs)


def gen_route_count_formula(*args: Any, **kwargs: Any) -> Any:
    return _gen_route_count_formula_impl(*args, **kwargs)


def generate_routes_network(*args: Any, **kwargs: Any) -> Any:
    return _generate_routes_network_impl(*args, **kwargs)


def build_heatmap(*args: Any, **kwargs: Any) -> Any:
    return _build_heatmap_impl(*args, **kwargs)


__all__ = [
    "build_heatmap",
    "compute_stop_volumes",
    "generate_routes_network",
    "gen_route_count_formula",
    "stops_area_km2",
]
