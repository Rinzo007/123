from __future__ import annotations

from dataclasses import dataclass
from math import exp, isclose

from .city import DemandZone
from .demand import DemandMatrix, ODPairDemand


@dataclass(frozen=True, slots=True)
class GravityParameters:
    speed_kph: float = 30.0
    decay: float = 0.08
    intrazonal_factor: float = 0.5

    def __post_init__(self) -> None:
        if self.speed_kph <= 0:
            raise ValueError("speed_kph must be positive")
        if self.decay <= 0:
            raise ValueError("decay must be positive")
        if not 0 < self.intrazonal_factor <= 1:
            raise ValueError("intrazonal_factor must be in (0, 1]")


def gravity_od(
    zones: tuple[DemandZone, ...],
    *,
    parameters: GravityParameters = GravityParameters(),
) -> DemandMatrix:
    """Гравитационная матрица трудовых поездок.

    Productions - это занятость зоны, то есть ровно столько поездок на
    работу, сколько в ней работников. Множителя вида `население x 0.12` здесь
    нет намеренно: он ничего не измерял, а оба эталонных бандла дают одну
    трудовую поездку на работника в сутки - у киевского residents равно jobs
    равно числу поездок, и столько же получается, если productions равно
    занятости. Там, где занятости нет (пустые jobs), productions берётся из
    населения, иначе матрица схлопнулась бы в ноль.
    """
    productions = {z.id: max(0.0, z.jobs) for z in zones}
    if sum(productions.values()) <= 0:
        productions = {z.id: max(0.0, z.population) for z in zones}
    attraction_base = {z.id: max(0.0, z.jobs) for z in zones}
    if sum(attraction_base.values()) <= 0:
        attraction_base = {z.id: max(0.0, z.population) for z in zones}
    total_attraction = sum(attraction_base.values())

    if not zones or total_attraction <= 0:
        return DemandMatrix(())

    raw: list[tuple[str, str, float, float]] = []
    for origin in zones:
        for destination in zones:
            distance_m = _distance_m(origin, destination)
            impedance_minutes = (
                parameters.intrazonal_factor
                if distance_m == 0
                else distance_m / 1000.0 / parameters.speed_kph * 60.0
            )
            friction = exp(-parameters.decay * impedance_minutes)
            weight = attraction_base[destination.id] * friction
            raw.append(
            (
                origin.id,
                destination.id,
                weight,
                impedance_minutes,
            )
        )

    pairs: list[ODPairDemand] = []
    by_origin: dict[str, float] = {}
    for origin_id, _, weight, _base_time_min in raw:
        by_origin[origin_id] = by_origin.get(origin_id, 0.0) + weight

    for origin_id, destination_id, weight, base_time_min in raw:
        total = by_origin[origin_id]
        trips = (
            0.0
            if isclose(total, 0.0, abs_tol=1e-12)
            else productions[origin_id] * weight / total
        )
        pairs.append(
            ODPairDemand(
                origin_id,
                destination_id,
                trips,
                base_time_min=base_time_min,
            )
        )

    return DemandMatrix(tuple(pairs))



def _distance_m(a: DemandZone, b: DemandZone) -> float:
    return ((a.centroid_x - b.centroid_x) ** 2 + (a.centroid_y - b.centroid_y) ** 2) ** 0.5
