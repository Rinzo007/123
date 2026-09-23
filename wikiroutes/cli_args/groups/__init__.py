"""Группы аргументов CLI по темам."""

from __future__ import annotations

from .basic import add_basic_args, add_misc_args
from .dedup import add_dedup_args
from .filters import add_filter_args
from .flow import add_flow_args
from .ghs import add_ghs_args
from .heatmap import add_heatmap_args
from .ideas import add_ideas_args
from .overture import add_overture_args

__all__ = [
    "add_basic_args",
    "add_dedup_args",
    "add_filter_args",
    "add_flow_args",
    "add_ghs_args",
    "add_heatmap_args",
    "add_ideas_args",
    "add_misc_args",
    "add_overture_args",
]