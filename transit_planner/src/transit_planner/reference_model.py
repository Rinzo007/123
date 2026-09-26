from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True, slots=True)
class ReferencePeriod:
    key: str
    start_minute: int
    end_minute: int
    outbound_share: float
    return_share: float


REFERENCE_PERIODS = (
    ReferencePeriod("early", 240, 360, 0.06, 0.01),
    ReferencePeriod("am", 360, 540, 0.60, 0.06),
    ReferencePeriod("mid", 540, 900, 0.20, 0.18),
    ReferencePeriod("pm", 900, 1140, 0.10, 0.55),
    ReferencePeriod("eve", 1140, 1440, 0.04, 0.20),
)


@dataclass(frozen=True, slots=True)
class ReferencePurposeLayer:
    key: str
    label: str
    trips_per_resource: float
    attraction_distance_m: float
    max_destinations: int
    outbound_shares: tuple[float, ...]
    return_shares: tuple[float, ...]

    def __post_init__(self) -> None:
        if len(self.outbound_shares) != len(REFERENCE_PERIODS):
            raise ValueError("Outbound profile must have five periods")
        if len(self.return_shares) != len(REFERENCE_PERIODS):
            raise ValueError("Return profile must have five periods")
        if self.trips_per_resource < 0:
            raise ValueError("trips_per_resource cannot be negative")
        if self.attraction_distance_m <= 0:
            raise ValueError("attraction_distance_m must be positive")
        if self.max_destinations <= 0:
            raise ValueError("max_destinations must be positive")


REFERENCE_PURPOSE_LAYERS = (
    ReferencePurposeLayer(
        "edu",
        "School or campus",
        0.16,
        1600.0,
        6,
        (0.04, 0.76, 0.14, 0.05, 0.01),
        (0.00, 0.02, 0.60, 0.32, 0.06),
    ),
    ReferencePurposeLayer(
        "health",
        "Hospital or clinic",
        0.06,
        3000.0,
        6,
        (0.08, 0.34, 0.36, 0.16, 0.06),
        (0.04, 0.12, 0.36, 0.32, 0.16),
    ),
    ReferencePurposeLayer(
        "shop",
        "Shops",
        0.34,
        2000.0,
        6,
        (0.01, 0.07, 0.44, 0.36, 0.12),
        (0.01, 0.03, 0.36, 0.42, 0.18),
    ),
    ReferencePurposeLayer(
        "air",
        "Airport",
        0.03,
        14000.0,
        2,
        (0.18, 0.24, 0.26, 0.20, 0.12),
        (0.06, 0.14, 0.26, 0.28, 0.26),
    ),
    ReferencePurposeLayer(
        "night",
        "Bar, café or venue",
        0.22,
        3000.0,
        6,
        (0.00, 0.02, 0.14, 0.32, 0.52),
        (0.02, 0.02, 0.08, 0.24, 0.64),
    ),
)


class TrackRow(StrEnum):
    MIXED = "mixed"
    RESERVED = "reserved"
    ELEVATED = "elevated"
    GRADE = "grade"


@dataclass(frozen=True, slots=True)
class ReferenceRowProfile:
    speed_kph: float
    cost_per_km: float


@dataclass(frozen=True, slots=True)
class ReferenceModeProfile:
    capacity: int
    opex_per_vehicle_km: float
    vehicle_cost_day: float
    dwell_s: float
    dwell_per_passenger_s: float
    jitter_s: float
    turnback_s: float
    track_capacity_per_hour: float
    access_m: float
    max_class: int
    passenger_per_square_m: float
    platform_m: float
    platform_cost_per_m: float
    acceleration_mps2: float
    comfortable_radius_m: float
    minimum_radius_m: float
    rows: dict[TrackRow, ReferenceRowProfile]
    default_row: TrackRow


@dataclass(frozen=True, slots=True)
class ReferenceTransferProfile:
    base_s: float = 405.0
    wait_multiplier: float = 1.0
    walk_multiplier: float = 1.0
    rider_bias_s: float = 0.0

    def __post_init__(self) -> None:
        if self.base_s < 0:
            raise ValueError("base_s cannot be negative")
        if self.wait_multiplier < 0 or self.walk_multiplier < 0:
            raise ValueError("transfer multipliers cannot be negative")
        if self.rider_bias_s < 0:
            raise ValueError("rider_bias_s cannot be negative")


@dataclass(frozen=True, slots=True)
class ReferenceCarProfile:
    cost_per_km_eur: float = 0.25
    parking_eur: float = 1.5
    parking_s: float = 240.0
    circuity: float = 1.3
@dataclass(frozen=True, slots=True)
class ReferenceMobilityProfile:
    two_wheel_speed_kph: float = 15.12
    two_wheel_reach_m: float = 7000.0
    two_wheel_per_km_eur: float = 0.03


REFERENCE_VOT_S_PER_EUR = 360.0
REFERENCE_TRANSFER = ReferenceTransferProfile()
REFERENCE_CAR = ReferenceCarProfile()
REFERENCE_MOBILITY = ReferenceMobilityProfile()


REFERENCE_MODE_PROFILES = {
    "bus": ReferenceModeProfile(
        90, 5.0, 250.0, 20.0, 2.0, 90.0, 60.0, 90.0,
        500.0, 3, 6.0, 0.0, 0.0, 1.2, 15.0, 8.0,
        {
            TrackRow.MIXED: ReferenceRowProfile(18.0, 0.4),
            TrackRow.RESERVED: ReferenceRowProfile(23.0, 2.5),
        },
        TrackRow.MIXED,
    ),
    "tram": ReferenceModeProfile(
        250, 9.0, 900.0, 25.0, 0.6, 60.0, 90.0, 40.0,
        600.0, 2, 6.5, 40.0, 0.031, 1.2, 30.0, 18.0,
        {
            TrackRow.MIXED: ReferenceRowProfile(19.0, 9.0),
            TrackRow.RESERVED: ReferenceRowProfile(25.0, 18.0),
            TrackRow.GRADE: ReferenceRowProfile(33.0, 85.0),
        },
        TrackRow.MIXED,
    ),
    "metro": ReferenceModeProfile(
        750, 14.0, 3200.0, 30.0, 0.15, 15.0, 150.0, 30.0,
        800.0, 3, 8.0, 100.0, 0.3, 1.0, 150.0, 90.0,
        {
            TrackRow.RESERVED: ReferenceRowProfile(70.0, 32.0),
            TrackRow.ELEVATED: ReferenceRowProfile(70.0, 62.0),
            TrackRow.GRADE: ReferenceRowProfile(70.0, 120.0),
        },
        TrackRow.RESERVED,
    ),
    "rail": ReferenceModeProfile(
        1000, 22.0, 5200.0, 45.0, 0.3, 25.0, 300.0, 20.0,
        1500.0, 3, 7.0, 140.0, 0.125, 0.8, 400.0, 150.0,
        {
            TrackRow.RESERVED: ReferenceRowProfile(58.0, 22.0),
            TrackRow.ELEVATED: ReferenceRowProfile(78.0, 48.0),
            TrackRow.GRADE: ReferenceRowProfile(78.0, 95.0),
        },
        TrackRow.RESERVED,
    ),
}


CROWDED_LOAD_RATIO = 1.0
SEVERE_LOAD_RATIO = 2.0
EXTREME_LOAD_RATIO = 4.0
