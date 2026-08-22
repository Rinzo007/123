"""Геометрические helper-стадии pipeline."""
from __future__ import annotations

import math
from typing import Any

from .filters import compute_bbox
from .models import RouteData


def compute_pipeline_bbox(base_routes: list[RouteData], buffer_deg: float | None) -> tuple[float, float, float, float] | None:
    valid = [route for route in base_routes if not route.error and route.directions]
    result = compute_bbox(valid, buffer_deg=buffer_deg)
    if result is None:
        return None
    return (result.min_lat, result.min_lon, result.max_lat, result.max_lon)


def route_inside_bbox(route: RouteData, bbox: tuple[float, float, float, float] | None) -> bool:
    if route.error or not route.directions or bbox is None:
        return False
    return all(
        direction.coords
        and all(
            math.isfinite(lat) and math.isfinite(lon)
            and bbox[0] <= lat <= bbox[2]
            and bbox[1] <= lon <= bbox[3]
            for lat, lon in direction.coords
        )
        for direction in route.directions
    )


def select_secondary_routes(loaded: list[RouteData], bbox: tuple[float, float, float, float] | None, no_bbox_filter: bool) -> list[RouteData]:
    if no_bbox_filter:
        return list(loaded)
    if bbox is None:
        return []
    return [route for route in loaded if route_inside_bbox(route, bbox)]

__all__ = ["compute_pipeline_bbox", "route_inside_bbox", "select_secondary_routes"]
