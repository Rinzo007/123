from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from json import load
from math import exp
from pathlib import Path

_MODEL_DATA = load(open(Path(__file__).with_name("model.json"), encoding="utf-8"))


@dataclass(frozen=True, slots=True)
class ReferencePeriod:
    key: str
    start_minute: int
    end_minute: int
    outbound_share: float
    return_share: float


REFERENCE_PERIODS = tuple(
    ReferencePeriod(*row) for row in _MODEL_DATA["periods"]
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


REFERENCE_PURPOSE_LAYERS = tuple(
    ReferencePurposeLayer(
        row[0], row[1], row[2], row[3], row[4], tuple(row[5]), tuple(row[6])
    )
    for row in _MODEL_DATA["purposes"]
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
    lateral_acceleration_mps2: float = 1.0
    infill_m: float = 0.0


@dataclass(frozen=True, slots=True)
class ReferenceTransferProfile:
    base_s: float = 405.0
    wait_multiplier: float = 1.0
    walk_multiplier: float = 1.0
    per_m_s: float = 0.25
    rider_bias_s: float = 0.0

    def __post_init__(self) -> None:
        if self.base_s < 0:
            raise ValueError("base_s cannot be negative")
        if self.wait_multiplier < 0 or self.walk_multiplier < 0:
            raise ValueError("transfer multipliers cannot be negative")
        if self.per_m_s < 0:
            raise ValueError("per_m_s cannot be negative")
        if self.rider_bias_s < 0:
            raise ValueError("rider_bias_s cannot be negative")


@dataclass(frozen=True, slots=True)
class ReferenceCarProfile:
    cost_per_km_eur: float = 0.25
    parking_eur: float = 1.5
    parking_s: float = 240.0
    circuity: float = 1.3


@dataclass(frozen=True, slots=True)
class ReferenceJourneyChoiceProfile:
    ride_weight: float = 1.0
    walk_weight: float = 1.65
    wait_weight: float = 1.72
    departure_shift_weight: float = 0.4
    driving_congestion_weight: float = 1.33
    parking_weight: float = 1.6

    def __post_init__(self) -> None:
        if self.walk_weight <= 0 or self.wait_weight <= 0 or self.ride_weight <= 0:
            raise ValueError("Journey choice time weights must be positive")
        if self.departure_shift_weight < 0:
            raise ValueError("departure_shift_weight cannot be negative")


@dataclass(frozen=True, slots=True)
class ReferenceTransitBurdenProfile:
    stage_access_weight: float = 1.65
    stage_egress_weight: float = 6.723
    stage_transfer_walk_weight: float = 9.906
    first_transfer_burden_min: float = 1.724
    multiple_transfer_burden_min: float = 8.165

    def __post_init__(self) -> None:
        if min(
            self.stage_access_weight,
            self.stage_egress_weight,
            self.stage_transfer_walk_weight,
        ) <= 0:
            raise ValueError("Walking stage weights must be positive")
        if (
            self.first_transfer_burden_min < 0
            or self.multiple_transfer_burden_min < self.first_transfer_burden_min
        ):
            raise ValueError("Multiple-transfer burden must be at least the first-transfer burden")

    def transfer_burden_minutes(self, transfers: int) -> float:
        if transfers <= 0:
            return 0.0
        if transfers == 1:
            return self.first_transfer_burden_min
        return self.multiple_transfer_burden_min


REFERENCE_VOT_S_PER_EUR = float(_MODEL_DATA["vot_s_per_eur"])
_modes = _MODEL_DATA["modes"]

REFERENCE_TRANSFER = ReferenceTransferProfile(**_MODEL_DATA["transfer"])
REFERENCE_CAR = ReferenceCarProfile(
    cost_per_km_eur=float(_MODEL_DATA["car"]["cost_per_km_eur"]),
    parking_eur=float(_MODEL_DATA["car"]["parking_eur"]),
    parking_s=float(_MODEL_DATA["car"]["parking_s"]),
    circuity=float(_MODEL_DATA["car"]["circuity"]),
)
REFERENCE_JOURNEY_CHOICE = ReferenceJourneyChoiceProfile(
    **{
        key: value
        for key, value in _MODEL_DATA.get("journey_choice", {}).items()
        if key != "source"
    }
)
REFERENCE_JOURNEY_CHOICE_TABLE8 = ReferenceJourneyChoiceProfile(
    **{
        key: value
        for key, value in _MODEL_DATA.get("journey_choice_table8", {}).items()
        if key != "source"
    }
)
REFERENCE_TRANSIT_BURDENS = ReferenceTransitBurdenProfile(
    **{
        key: value
        for key, value in _MODEL_DATA.get("transit_burdens", {}).items()
        if key != "source"
    }
)


def headway_unevenness_factor(
    mode: str,
    headway_min: float,
    period_hours: float,
    stop_boardings: tuple[float, ...] = (),
    *,
    route_closed: bool = False,
    both_ways: bool = True,
) -> float:
    """Return the reference demand-feedback multiplier for headway."""
    if headway_min <= 0 or period_hours <= 0:
        raise ValueError("headway_min and period_hours must be positive")
    if any(boardings < 0 for boardings in stop_boardings):
        raise ValueError("stop_boardings cannot be negative")

    profile = REFERENCE_MODE_PROFILES[mode]
    direction_factor = (
        2.0
        if not route_closed or both_ways
        else 1.0
    )
    f = sum(
        profile.dwell_per_passenger_s * boardings
        / (2.0 * direction_factor * period_hours * 3600.0)
        for boardings in stop_boardings
    )
    m = min(
        1.0,
        profile.jitter_s * exp(f)
        / (headway_min * 60.0),
    )
    return 1.0 + m * m


def minimum_station_headway_min(
    mode: str,
    *,
    route_closed: bool,
    boardings_per_hour: float = 0.0,
) -> tuple[float, str]:
    """Return the reference minimum station headway and limiting mechanism."""
    profile = REFERENCE_MODE_PROFILES[mode]
    available_dwell_s = 60.0 - profile.dwell_per_passenger_s * max(
        0.0,
        boardings_per_hour,
    ) / 60.0
    dwell_headway = (
        (profile.dwell_s + 25.0) / available_dwell_s
        if available_dwell_s > 6.0
        else 999.0
    )
    turnback_headway = 0.0 if route_closed else profile.turnback_s / 120.0
    if turnback_headway > dwell_headway:
        return turnback_headway, "turnback"
    return dwell_headway, "dwell"


def crowding_time_multiplier(load_ratio: float) -> float:
    """Return the active reference crowding multiplier for in-vehicle time."""
    if load_ratio < 0:
        raise ValueError("load_ratio cannot be negative")
    return 1.0 + max(
        0.0,
        min(load_ratio, 1.5) - 0.85,
    ) * 2.2


def lateral_speed_limit_kph(
    mode: str,
    radius_m: float,
) -> float:
    """Return the speed limit imposed by reference lateral acceleration."""
    if radius_m < 0:
        raise ValueError("radius_m cannot be negative")
    profile = REFERENCE_MODE_PROFILES[mode]
    if radius_m == 0.0:
        return 0.0
    return (profile.lateral_acceleration_mps2 * radius_m) ** 0.5 * 3.6


def minimum_track_headway_min(
    capacity_departures_per_hour: float | None,
) -> float:
    """Return the minimum interval imposed by a track's tph capacity."""
    if capacity_departures_per_hour is None or capacity_departures_per_hour <= 0:
        return float("inf")
    return 60.0 / capacity_departures_per_hour


REFERENCE_MODE_PROFILES = {
    mode: ReferenceModeProfile(
        capacity=int(data["capacity"]),
        opex_per_vehicle_km=float(data["opex_per_vehicle_km"]),
        vehicle_cost_day=float(data["vehicle_cost_day"]),
        dwell_s=float(data["dwell_s"]),
        dwell_per_passenger_s=float(data["dwell_per_passenger_s"]),
        jitter_s=float(data["jitter_s"]),
        turnback_s=float(data["turnback_s"]),
        track_capacity_per_hour=float(data["track_capacity_per_hour"]),
        access_m=float(data["access_m"]),
        max_class=int(data["max_class"]),
        passenger_per_square_m=float(data["passenger_per_square_m"]),
        platform_m=float(data["platform_m"]),
        platform_cost_per_m=float(data["platform_cost_per_m"]),
        acceleration_mps2=float(data["acceleration_mps2"]),
        comfortable_radius_m=float(data["comfortable_radius_m"]),
        minimum_radius_m=float(data["minimum_radius_m"]),
        rows={
            TrackRow(row): ReferenceRowProfile(float(values[0]), float(values[1]))
            for row, values in data["rows"].items()
        },
        default_row=TrackRow(data["default_row"]),
        lateral_acceleration_mps2=float(data["lateral_acceleration_mps2"]),
        infill_m=float(data["infill_m"]),
    )
    for mode, data in _modes.items()
}




CROWDED_LOAD_RATIO = 1.0
SEVERE_LOAD_RATIO = 2.0
EXTREME_LOAD_RATIO = 4.0
