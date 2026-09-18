"""Неизменяемый контекст вычислений, разделяемый между потоками."""

from dataclasses import dataclass

import numpy as np
from shapely import STRtree


# ---------------------------------------------------------------------------
# Контекст вычислений (shared между потоками, read-only)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class _OvertureContext:
    """Неизменяемый контекст вычислений, разделяемый между потоками."""

    polygon_geometries: np.ndarray
    tree: STRtree
    epsg: int
    buffer_m: float
    city: str
    sig: str
    assume_no_overlap: bool = False
    use_coverage_union: bool = False
    union_grid_size: float | None = None
