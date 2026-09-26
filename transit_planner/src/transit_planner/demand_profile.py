from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class TripPurpose(StrEnum):
    WORK = "work"
    EDUCATION = "education"
    SHOPPING = "shopping"
    HEALTH = "health"
    LEISURE = "leisure"
    AIRPORT = "airport"


DEFAULT_PERIOD_IDS = (
    "night",
    "morning_peak",
    "daytime",
    "evening_peak",
    "late_evening",
)



@dataclass(frozen=True, slots=True)
class DemandPeriod:
    id: str
    start_minute: int
    end_minute: int

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("Demand period id cannot be empty")
        if not 0 <= self.start_minute < self.end_minute <= 1440:
            raise ValueError("Invalid demand period window")


DEFAULT_DEMAND_PERIODS = (
    DemandPeriod("night", 0, 360),
    DemandPeriod("morning_peak", 360, 600),
    DemandPeriod("daytime", 600, 960),
    DemandPeriod("evening_peak", 960, 1200),
    DemandPeriod("late_evening", 1200, 1440),
)


@dataclass(frozen=True, slots=True)
class PurposeProfile:
    purpose: TripPurpose
    daily_share: float
    period_shares: dict[str, float]

    def __post_init__(self) -> None:
        if not 0.0 <= self.daily_share <= 1.0:
            raise ValueError("daily_share must be between 0 and 1")
        if not self.period_shares:
            raise ValueError("period_shares cannot be empty")
        if any(value < 0.0 for value in self.period_shares.values()):
            raise ValueError("Period shares cannot be negative")
        total = sum(self.period_shares.values())
        if abs(total - 1.0) > 1e-9:
            raise ValueError("Period shares must sum to 1")


@dataclass(frozen=True, slots=True)
class TemporalDemandProfile:
    purposes: tuple[PurposeProfile, ...]
    period_ids: tuple[str, ...] = DEFAULT_PERIOD_IDS

    def __post_init__(self) -> None:
        if not self.period_ids:
            raise ValueError("At least one demand period is required")
        if len(set(self.period_ids)) != len(self.period_ids):
            raise ValueError("Demand period ids must be unique")

        total = sum(profile.daily_share for profile in self.purposes)
        if total <= 0.0:
            raise ValueError("At least one purpose must have positive daily share")
        if abs(total - 1.0) > 1e-9:
            raise ValueError("Purpose daily shares must sum to 1")

        for profile in self.purposes:
            if set(profile.period_shares) != set(self.period_ids):
                raise ValueError(
                    f"Purpose {profile.purpose.value} must define all demand periods"
                )

    def purpose_share(self, purpose: str | TripPurpose) -> float:
        value = TripPurpose(purpose)
        for profile in self.purposes:
            if profile.purpose == value:
                return profile.daily_share
        return 0.0

    def period_share(
        self,
        purpose: str | TripPurpose,
        period_id: str,
    ) -> float:
        value = TripPurpose(purpose)
        for profile in self.purposes:
            if profile.purpose == value:
                return profile.period_shares[period_id]
        return 0.0


DEFAULT_TEMPORAL_DEMAND_PROFILE = TemporalDemandProfile(
    purposes=(
        PurposeProfile(
            TripPurpose.WORK,
            0.40,
            {
                "night": 0.05,
                "morning_peak": 0.35,
                "daytime": 0.20,
                "evening_peak": 0.30,
                "late_evening": 0.10,
            },
        ),
        PurposeProfile(
            TripPurpose.EDUCATION,
            0.15,
            {
                "night": 0.05,
                "morning_peak": 0.45,
                "daytime": 0.20,
                "evening_peak": 0.25,
                "late_evening": 0.05,
            },
        ),
        PurposeProfile(
            TripPurpose.SHOPPING,
            0.15,
            {
                "night": 0.02,
                "morning_peak": 0.08,
                "daytime": 0.35,
                "evening_peak": 0.40,
                "late_evening": 0.15,
            },
        ),
        PurposeProfile(
            TripPurpose.HEALTH,
            0.08,
            {
                "night": 0.01,
                "morning_peak": 0.15,
                "daytime": 0.55,
                "evening_peak": 0.24,
                "late_evening": 0.05,
            },
        ),
        PurposeProfile(
            TripPurpose.LEISURE,
            0.18,
            {
                "night": 0.02,
                "morning_peak": 0.04,
                "daytime": 0.25,
                "evening_peak": 0.40,
                "late_evening": 0.29,
            },
        ),
        PurposeProfile(
            TripPurpose.AIRPORT,
            0.04,
            {
                "night": 0.12,
                "morning_peak": 0.20,
                "daytime": 0.28,
                "evening_peak": 0.28,
                "late_evening": 0.12,
            },
        ),
    ),
)
