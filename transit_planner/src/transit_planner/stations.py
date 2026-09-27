from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class PlatformLayout(StrEnum):
    SIDE = "side"
    ISLAND = "island"
    CENTER = "center"
    EXPRESS_LOCAL = "express_local"


@dataclass(frozen=True, slots=True)
class Platform:
    id: str
    station_id: str
    length_m: float
    track_ids: tuple[str, ...] = ()
    layout: PlatformLayout = PlatformLayout.SIDE
    number: int = 1

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.station_id.strip():
            raise ValueError("Platform id and station_id cannot be empty")
        if self.length_m <= 0:
            raise ValueError("Platform length must be positive")
        if self.number <= 0:
            raise ValueError("Platform number must be positive")


@dataclass(frozen=True, slots=True)
class StationGroup:
    id: str
    name: str
    station_ids: tuple[str, ...]
    transfer_walk_min: float = 0.0

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.name.strip():
            raise ValueError("Station group id and name cannot be empty")
        if len(self.station_ids) < 2:
            raise ValueError("Station group needs at least two stations")
        if len(self.station_ids) != len(set(self.station_ids)):
            raise ValueError("Station group station_ids must be unique")
        if self.transfer_walk_min < 0:
            raise ValueError("transfer_walk_min cannot be negative")


@dataclass(frozen=True, slots=True)
class Station:
    id: str
    name: str
    stop_id: str
    platform_ids: tuple[str, ...] = ()
    group_id: str | None = None
    interchange: bool = False
    platform_length_m: float | None = None

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.name.strip() or not self.stop_id.strip():
            raise ValueError("Station id, name and stop_id cannot be empty")
        if self.platform_length_m is not None and self.platform_length_m <= 0:
            raise ValueError("platform_length_m must be positive")
