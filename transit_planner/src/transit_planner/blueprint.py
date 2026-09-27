from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable

from .infrastructure import TrackSection


@dataclass(frozen=True, slots=True)
class TrackBlueprint:
    id: str
    sections: tuple[TrackSection, ...]

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("Track id cannot be empty")
        if not self.sections:
            raise ValueError("Track needs at least one section")
