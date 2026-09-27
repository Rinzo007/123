from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from math import hypot


class Elevation(StrEnum):
    SURFACE = "surface"
    TRENCHED = "trenched"
    ELEVATED = "elevated"
    RAMP = "ramp"
    TUNNEL = "tunnel"


@dataclass(frozen=True, slots=True)
class TrackGeometry:
    id: str
    node_ids: tuple[str, ...]
    length_m: float
    curve_radius_m: float | None = None
    straight: bool = False

    def __post_init__(self) -> None:
        if not self.id.strip() or len(self.node_ids) < 2:
            raise ValueError("Track geometry needs an id and at least two nodes")
        if self.length_m <= 0:
            raise ValueError("Track geometry length must be positive")
        if self.curve_radius_m is not None and self.curve_radius_m <= 0:
            raise ValueError("Curve radius must be positive")


@dataclass(frozen=True, slots=True)
class GradeCrossing:
    id: str
    track_section_id: str
    speed_limit_kph: float
    maintenance_cost_year: float = 0.0

    def __post_init__(self) -> None:
        if self.speed_limit_kph <= 0 or self.maintenance_cost_year < 0:
            raise ValueError("Invalid grade crossing parameters")


@dataclass(frozen=True, slots=True)
class Portal:
    id: str
    track_section_id: str
    position: float
    construction_cost: float

    def __post_init__(self) -> None:
        if not 0 <= self.position <= 1:
            raise ValueError("Portal position must be between 0 and 1")
        if self.construction_cost < 0:
            raise ValueError("Portal construction cost cannot be negative")


@dataclass(frozen=True, slots=True)
class Bond:
    id: str
    principal: float
    annual_interest_rate: float
    term_years: int
    remaining_principal: float | None = None

    def __post_init__(self) -> None:
        if self.principal <= 0 or self.annual_interest_rate < 0 or self.term_years <= 0:
            raise ValueError("Invalid bond")
        if self.remaining_principal is not None and not 0 <= self.remaining_principal <= self.principal:
            raise ValueError("Invalid remaining principal")

    @property
    def outstanding(self) -> float:
        return self.principal if self.remaining_principal is None else self.remaining_principal

    @property
    def annual_interest(self) -> float:
        return self.outstanding * self.annual_interest_rate


@dataclass(frozen=True, slots=True)
class FinancialSnapshot:
    year: int
    revenue: float
    operating_cost: float
    maintenance_cost: float
    capital_spend: float
    interest: float
    cash_flow: float


@dataclass(slots=True)
class FinancialLedger:
    snapshots: list[FinancialSnapshot] = field(default_factory=list)
    bonds: dict[str, Bond] = field(default_factory=dict)

    def add_snapshot(self, snapshot: FinancialSnapshot) -> None:
        if snapshot.year < 0:
            raise ValueError("year cannot be negative")
        if any(x.year == snapshot.year for x in self.snapshots):
            raise ValueError("Duplicate financial year")
        self.snapshots.append(snapshot)

    def add_bond(self, bond: Bond) -> None:
        if bond.id in self.bonds:
            raise ValueError(f"Duplicate bond: {bond.id}")
        self.bonds[bond.id] = bond

    @property
    def debt(self) -> float:
        return sum(bond.outstanding for bond in self.bonds.values())

    @property
    def annual_interest(self) -> float:
        return sum(bond.annual_interest for bond in self.bonds.values())


@dataclass(frozen=True, slots=True)
class RouteFinancials:
    route_id: str
    revenue: float
    operating_cost: float
    maintenance_cost: float
    passenger_trips: float

    @property
    def net_operating_result(self) -> float:
        return self.revenue - self.operating_cost - self.maintenance_cost


@dataclass(frozen=True, slots=True)
class HourlyLoad:
    route_id: str
    hour: int
    direction: str
    passengers: float
    capacity: float

    @property
    def load_factor(self) -> float:
        return 0.0 if self.capacity <= 0 else self.passengers / self.capacity


@dataclass(frozen=True, slots=True)
class IncomeModeShare:
    income_group: str
    transit: float
    car: float
    walk: float
    bike: float

    @property
    def total(self) -> float:
        return self.transit + self.car + self.walk + self.bike


@dataclass(frozen=True, slots=True)
class LifetimeStats:
    days: int = 0
    passengers: float = 0.0
    passenger_km: float = 0.0
    revenue: float = 0.0
    operating_cost: float = 0.0

    def advance(self, *, passengers: float, passenger_km: float, revenue: float, operating_cost: float) -> "LifetimeStats":
        if min(passengers, passenger_km, revenue, operating_cost) < 0:
            raise ValueError("Lifetime values cannot be negative")
        return LifetimeStats(
            self.days + 1,
            self.passengers + passengers,
            self.passenger_km + passenger_km,
            self.revenue + revenue,
            self.operating_cost + operating_cost,
        )


def track_length(nodes: tuple[tuple[float, float], ...]) -> float:
    if len(nodes) < 2:
        raise ValueError("At least two nodes are required")
    return sum(hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(nodes, nodes[1:]))
