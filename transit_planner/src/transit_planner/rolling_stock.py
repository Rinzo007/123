from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RollingStockType:
    id: str
    name: str
    vehicle_type_id: str
    car_capacity: int
    car_length_m: float
    train_width_m: float
    max_cars: int
    max_speed_kph: float
    acceleration_mps2: float
    deceleration_mps2: float
    lateral_acceleration_mps2: float
    minimum_curve_radius_m: float
    dwell_seconds: float
    car_cost: float = 0.0
    train_operating_cost_per_hour: float = 0.0
    car_operating_cost_per_hour: float = 0.0
    track_maintenance_cost_per_km_year: float = 0.0
    station_maintenance_cost_per_year: float = 0.0
    tph_limit: float = 60.0

    def __post_init__(self) -> None:
        numeric = {
            "car_capacity": self.car_capacity, "car_length_m": self.car_length_m,
            "train_width_m": self.train_width_m, "max_cars": self.max_cars,
            "max_speed_kph": self.max_speed_kph, "acceleration_mps2": self.acceleration_mps2,
            "deceleration_mps2": self.deceleration_mps2,
            "lateral_acceleration_mps2": self.lateral_acceleration_mps2,
            "minimum_curve_radius_m": self.minimum_curve_radius_m,
            "dwell_seconds": self.dwell_seconds, "car_cost": self.car_cost,
            "train_operating_cost_per_hour": self.train_operating_cost_per_hour,
            "car_operating_cost_per_hour": self.car_operating_cost_per_hour,
            "track_maintenance_cost_per_km_year": self.track_maintenance_cost_per_km_year,
            "station_maintenance_cost_per_year": self.station_maintenance_cost_per_year,
            "tph_limit": self.tph_limit,
        }
        if not self.id.strip() or not self.name.strip() or not self.vehicle_type_id.strip():
            raise ValueError("Rolling stock identifiers cannot be empty")
        if any(value <= 0 for value in numeric.values()):
            raise ValueError("Rolling stock physical and cost parameters must be positive")

    @property
    def capacity(self) -> int:
        return self.car_capacity * self.max_cars

    @property
    def train_length_m(self) -> float:
        return self.car_length_m * self.max_cars
