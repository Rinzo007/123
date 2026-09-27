from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from math import floor


class FareSystem(StrEnum):
    FLAT = "flat"
    ROUTE = "route"
    DISTANCE = "distance"
    ZONE = "zone"


class TransferPolicy(StrEnum):
    NONE = "none"
    FREE = "free"
    TIME_WINDOW = "time_window"


@dataclass(frozen=True, slots=True)
class FareZone:
    id: str
    name: str
    zone_number: int

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.name.strip():
            raise ValueError("Fare zone identifiers cannot be empty")
        if self.zone_number <= 0:
            raise ValueError("zone_number must be positive")


@dataclass(frozen=True, slots=True)
class FareGroup:
    id: str
    name: str
    fare_system: FareSystem = FareSystem.FLAT
    flat_fare: float = 0.0
    route_fares: dict[str, float] = field(default_factory=dict)
    transfer_policy: TransferPolicy = TransferPolicy.NONE
    transfer_window_min: float = 0.0
    boarding_charge: float = 0.0
    per_km_rate: float = 0.0
    fare_cap: float | None = None
    zones: tuple[FareZone, ...] = ()
    zone_base_fare: float = 0.0
    zone_per_zone_fare: float = 0.0

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.name.strip():
            raise ValueError("Fare group identifiers cannot be empty")
        values = [self.flat_fare, *self.route_fares.values(), self.transfer_window_min,
                  self.boarding_charge, self.per_km_rate, self.zone_base_fare, self.zone_per_zone_fare]
        if self.fare_cap is not None:
            values.append(self.fare_cap)
        if any(value < 0 for value in values):
            raise ValueError("Fare values cannot be negative")
        if self.transfer_policy == TransferPolicy.TIME_WINDOW and self.transfer_window_min <= 0:
            raise ValueError("Time-window transfers require a positive window")

    def price(self, *, route_id: str | None = None, distance_km: float = 0.0,
              zone_count: int = 1, transfer: bool = False) -> float:
        if transfer and self.transfer_policy in (TransferPolicy.FREE, TransferPolicy.TIME_WINDOW):
            return 0.0
        if distance_km < 0 or zone_count < 1:
            raise ValueError("distance_km and zone_count are invalid")
        if self.fare_system == FareSystem.FLAT:
            value = self.flat_fare
        elif self.fare_system == FareSystem.ROUTE:
            value = self.route_fares.get(route_id or "", self.flat_fare)
        elif self.fare_system == FareSystem.DISTANCE:
            value = self.boarding_charge + distance_km * self.per_km_rate
        else:
            value = self.zone_base_fare + max(0, zone_count - 1) * self.zone_per_zone_fare
        if self.fare_cap is not None:
            value = min(value, self.fare_cap)
        if self.fare_system in (FareSystem.DISTANCE, FareSystem.ZONE):
            value = floor(value * 20.0 + 0.5) / 20.0
        return max(0.0, value)
