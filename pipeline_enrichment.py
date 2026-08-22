"""Adapters for external enrichment stages used by the pipeline.

This module deliberately contains no orchestration policy.  It exposes the
same call contracts used by ``pipeline_runtime`` while giving the enrichment
layer an explicit module boundary for GHS/GHS-S, Overture and POI.
"""

from __future__ import annotations

from typing import Any

from .ghs import compute_ghs as _compute_ghs
from .ghs import compute_ghs_s as _compute_ghs_s
from .overture import compute_overture as _compute_overture
from .poi import compute_poi as _compute_poi


def compute_ghs(*args: Any, **kwargs: Any) -> Any:
    return _compute_ghs(*args, **kwargs)


def compute_ghs_s(*args: Any, **kwargs: Any) -> Any:
    return _compute_ghs_s(*args, **kwargs)


def compute_overture(*args: Any, **kwargs: Any) -> Any:
    return _compute_overture(*args, **kwargs)


def compute_poi(*args: Any, **kwargs: Any) -> Any:
    return _compute_poi(*args, **kwargs)


__all__ = ["compute_ghs", "compute_ghs_s", "compute_overture", "compute_poi"]
