from __future__ import annotations

from dataclasses import dataclass, replace
from math import exp

from . import game_rules
from .reference_model import (
    REFERENCE_CAR,
    REFERENCE_JOURNEY_CHOICE,
    REFERENCE_TRANSIT_BURDENS,
    REFERENCE_TRANSFER,
    REFERENCE_VOT_S_PER_EUR,
)


@dataclass(frozen=True, slots=True)
class ChoiceConfig:
    """Mode-choice parameters used by the planner's main demand model.

    Walking-stage and transfer burdens follow Ha, Lee & Ko (2020); walk/wait
    multipliers follow Wardman et al. (2026).
    """

    value_of_time_s_per_eur: float = REFERENCE_VOT_S_PER_EUR
    transit_constant: float = 0.0
    car_constant: float = 0.0
    walk_constant: float = 0.0
    transit_fare_weight: float = 0.0
    transit_wait_weight: float = REFERENCE_JOURNEY_CHOICE.wait_weight
    transit_bias_minutes: float = REFERENCE_TRANSFER.rider_bias_s / 60.0
    transit_stage_access_weight: float = REFERENCE_TRANSIT_BURDENS.stage_access_weight
    transit_stage_egress_weight: float = REFERENCE_TRANSIT_BURDENS.stage_egress_weight
    transit_stage_transfer_walk_weight: float = (
        REFERENCE_TRANSIT_BURDENS.stage_transfer_walk_weight
    )
    transit_burden_first_min: float = REFERENCE_TRANSIT_BURDENS.first_transfer_burden_min
    transit_burden_multiple_min: float = (
        REFERENCE_TRANSIT_BURDENS.multiple_transfer_burden_min
    )
    car_cost_per_km_eur: float = REFERENCE_CAR.cost_per_km_eur
    car_parking_eur: float = REFERENCE_CAR.parking_eur
    car_parking_minutes: float = REFERENCE_CAR.parking_s / 60.0
    car_circuity: float = REFERENCE_CAR.circuity
    walk_circuity: float = 1.33 * REFERENCE_TRANSFER.walk_multiplier
    walk_speed_kph: float = game_rules.WALKING_SPEED_KPH
    min_sensible_driving_m: float = game_rules.MIN_SENSIBLE_DRIVING_DISTANCE_M
    arrival_gap_minutes: float = game_rules.ARRIVAL_GAP_S / 60.0

    def __post_init__(self) -> None:
        if self.value_of_time_s_per_eur <= 0:
            raise ValueError("value_of_time_s_per_eur must be positive")
        if self.transit_fare_weight < 0 or self.transit_wait_weight < 0:
            raise ValueError("Transit weights cannot be negative")
        if self.transit_bias_minutes < 0:
            raise ValueError("transit_bias_minutes cannot be negative")
        if min(
            self.transit_stage_access_weight,
            self.transit_stage_egress_weight,
            self.transit_stage_transfer_walk_weight,
        ) <= 0:
            raise ValueError("Transit walking stage weights must be positive")
        if (
            self.transit_burden_first_min < 0
            or self.transit_burden_multiple_min < self.transit_burden_first_min
        ):
            raise ValueError("Transit transfer burdens are invalid")
        if self.car_cost_per_km_eur < 0:
            raise ValueError("Mode operating cost cannot be negative")
        if self.car_parking_eur < 0 or self.car_parking_minutes < 0:
            raise ValueError("Parking cost and time cannot be negative")
        if self.car_circuity <= 0 or self.walk_circuity <= 0:
            raise ValueError("Circuity factors must be positive")
        if self.walk_speed_kph <= 0:
            raise ValueError("Walking speed must be positive")
        if self.min_sensible_driving_m < 0:
            raise ValueError("min_sensible_driving_m cannot be negative")
        if self.arrival_gap_minutes < 0:
            raise ValueError("arrival_gap_minutes cannot be negative")

    @property
    def time_coefficient(self) -> float:
        return 60.0 / self.value_of_time_s_per_eur

    def for_income(self, income_per_year: float) -> ChoiceConfig:
        """Копия конфига с VOT по доходу конкретного человека.

        В игре ценность времени разная у каждого жителя: VOT = доход / 1860
        часов. Здесь доход передаётся явно, потому что в планировщике сеть
        общая, а не персональная.
        """
        return replace(
            self,
            value_of_time_s_per_eur=max(
                1e-6, game_rules.value_of_time_s_per_eur(income_per_year)
            ),
        )

    def transfer_burden_minutes(self, transfers: int) -> float:
        if transfers <= 0:
            return 0.0
        if transfers == 1:
            return self.transit_burden_first_min
        return self.transit_burden_multiple_min

    def transit_generalized_minutes(
        self,
        *,
        in_vehicle_min: float,
        wait_min: float = 0.0,
        access_walk_min: float = 0.0,
        egress_walk_min: float = 0.0,
        transfer_walk_min: float = 0.0,
        transfers: int = 0,
    ) -> float:
        """Generalized transit cost in in-vehicle-time minutes.

        Stage walking weights already include the Wardman walk multiplier;
        the non-linear 1 vs 2+ transfer burden follows Ha et al. (2020).
        """
        return (
            max(0.0, in_vehicle_min)
            + self.transit_wait_weight * max(0.0, wait_min)
            + self.transit_stage_access_weight * max(0.0, access_walk_min)
            + self.transit_stage_egress_weight * max(0.0, egress_walk_min)
            + self.transit_stage_transfer_walk_weight * max(0.0, transfer_walk_min)
            + self.transfer_burden_minutes(transfers)
            + self.arrival_gap_minutes * transfers
            + self.transit_bias_minutes
        )


@dataclass(frozen=True, slots=True)
class ModeUtilities:
    walk: float
    car: float
    transit: float


def utilities(
    *,
    walk_time_min: float,
    car_time_min: float,
    transit_time_min: float | None,
    transit_wait_min: float = 0.0,
    transit_fare: float = 0.0,
    car_distance_km: float = 0.0,
    transit_access_walk_min: float = 0.0,
    transit_egress_walk_min: float = 0.0,
    transit_transfer_walk_min: float = 0.0,
    transit_transfers: int = 0,
    config: ChoiceConfig = ChoiceConfig(),
) -> ModeUtilities:
    coefficient = config.time_coefficient
    transit = (
        float("-inf")
        if transit_time_min is None
        else (
            config.transit_constant
            - coefficient * config.transit_generalized_minutes(
                in_vehicle_min=transit_time_min,
                wait_min=transit_wait_min,
                access_walk_min=transit_access_walk_min,
                egress_walk_min=transit_egress_walk_min,
                transfer_walk_min=transit_transfer_walk_min,
                transfers=transit_transfers,
            )
            - config.transit_fare_weight * transit_fare
        )
    )
    walk_generalized_minutes = config.walk_circuity * walk_time_min
    car_distance_m = max(0.0, car_distance_km) * 1000.0
    # Короткая поездка неудобна: заводить, разгоняться, искать парковку, -
    # поэтому время вождения умножается на коэффициент из игры. При
    # неизвестном расстоянии коэффициент не применяется: ноль километров
    # означает «нет данных», а не «поездка нулевой длины».
    if config.min_sensible_driving_m > 0.0 and car_distance_m > 0.0:
        short_drive_multiplier = game_rules.short_drive_penalty(car_distance_m)
    else:
        short_drive_multiplier = 1.0
    car_generalized_minutes = (
        config.car_circuity * car_time_min * short_drive_multiplier
        + config.car_parking_minutes
        + (
            car_distance_km * config.car_cost_per_km_eur
            + config.car_parking_eur
        ) * config.value_of_time_s_per_eur / 60.0
    )
    return ModeUtilities(
        walk=config.walk_constant - coefficient * walk_generalized_minutes,
        car=config.car_constant - coefficient * car_generalized_minutes,
        transit=transit,
    )


def probabilities(values: ModeUtilities) -> dict[str, float]:
    entries = (
        ("transit", values.transit),
        ("car", values.car),
        ("walk", values.walk),
    )
    finite_values = tuple(value for _, value in entries if value != float("-inf"))
    if not finite_values:
        return {key: 0.0 for key, _ in entries}
    maximum = max(finite_values)
    weights = {
        key: 0.0 if value == float("-inf") else exp(value - maximum)
        for key, value in entries
    }

    total = sum(weights.values())
    if total <= 0.0:
        return {key: 0.0 for key, _ in entries}

    return {
        "transit": weights["transit"] / total,
        "car": weights["car"] / total,
        "walk": weights["walk"] / total,
    }


def game_probabilities(
    *,
    walk_time_min: float,
    car_time_min: float,
    transit_in_vehicle_min: float | None,
    transit_wait_min: float = 0.0,
    transit_fare: float = 0.0,
    car_distance_km: float = 0.0,
    transit_access_walk_min: float = 0.0,
    transit_egress_walk_min: float = 0.0,
    transit_transfer_walk_min: float = 0.0,
    transit_transfers: int = 0,
    transit_available: bool = True,
    population: int = game_rules.INCOME_LADDER_SIZE,
    apply_min_transit_choice: bool = True,
) -> dict[str, float]:
    """Модальный сплит по правилу игры: минимум обобщённой стоимости.

    Разбор (popCommuteWorker:38701 getModeChoice, :38762 getModeChoiceForPerson):
    для каждого человека ячейки берётся минимум из

        авто    = (t_car * VOT + $cost) * shortTripPenalty
        транзит = t_transit * VOT + fare
        пешком  = t_walk * VOT

    Случайной полезности в игре нет, поэтому и здесь её нет: доля режима
    получается интегрированием по детерминированной лестнице дохода из
    `game_rules.income_ladder`. Так как VOT пропорционален доходу,
    необеспеченный пассажир выбирает транспорт, а обеспеченный - авто,
    что в логите с одним общим VOT было невозможно.

    Велосипед - добавка планировщика, в игре его нет. Он отбирается из доли
    ходьбы по отношению воспринимаемых стоимостей, поэтому велосипед не
    увеличивает активный спрос, а только перераспределяет его.
    """
    perceived = game_rules.perceived_minutes(
        in_vehicle_s=(transit_in_vehicle_min or 0.0) * 60.0,
        wait_s=transit_wait_min * 60.0,
        access_walk_s=transit_access_walk_min * 60.0,
        egress_walk_s=transit_egress_walk_min * 60.0,
        transfer_walk_s=transit_transfer_walk_min * 60.0,
        transfers=transit_transfers,
        car_drive_s=car_time_min * 60.0,
        car_parking_s=0.0,
        car_distance_m=car_distance_km * 1000.0,
        walk_s=walk_time_min * 60.0,
    )
    split = game_rules.game_mode_split(
        perceived,
        car_distance_m=car_distance_km * 1000.0,
        transit_fare=transit_fare,
        transit_available=transit_available,
        population=population,
        apply_min_transit_choice=apply_min_transit_choice,
    )
    return {
        "transit": split["transit"],
        "car": split["car"],
        "walk": split["walk"],
    }




def alternative_probabilities(
    generalized_costs: tuple[float, ...],
) -> tuple[float, ...]:
    """Split transit demand by inverse generalized travel cost.

    Costs must already include stage-weighted walking, wait and transfer
    burden (see ChoiceConfig.transit_generalized_minutes).
    """
    if not generalized_costs:
        return ()
    weights = tuple(1.0 / max(1.0, value) for value in generalized_costs)
    total = sum(weights)
    return (
        tuple(weight / total for weight in weights)
        if total > 0.0
        else tuple(0.0 for _ in weights)
    )
