from __future__ import annotations

from dataclasses import dataclass
from math import exp, hypot

from .city import DemandZone
from .demand import DemandMatrix, ODPairDemand, PeriodODPairDemand, TemporalDemandMatrix
from .od import GravityParameters, gravity_od
from .reference_model import REFERENCE_PERIODS, REFERENCE_PURPOSE_LAYERS, ReferencePurposeLayer


_REFERENCE_ATTRACTION_ALIASES: dict[str, tuple[str, ...]] = {
    "edu": ("edu", "education"),
    "health": ("health",),
    "shop": ("shop", "shopping"),
    "air": ("air", "airport"),
    "night": ("night", "leisure"),
}


def _purpose_attraction(zone: DemandZone, purpose: ReferencePurposeLayer) -> float:
    keys = _REFERENCE_ATTRACTION_ALIASES.get(purpose.key, (purpose.key,))
    return sum(zone.attractions.get(key, 0.0) for key in keys)


@dataclass(frozen=True, slots=True)
class ReferenceDemandLayerResult:
    purpose: str
    label: str
    demand: TemporalDemandMatrix


@dataclass(frozen=True, slots=True)
class ReferenceDemandLayers:
    layers: tuple[ReferenceDemandLayerResult, ...]

    @property
    def total_trips(self) -> float:
        return sum(layer.demand.total_trips for layer in self.layers)

    @property
    def by_period(self) -> dict[str, float]:
        totals: dict[str, float] = {}
        for layer in self.layers:
            for period, value in layer.demand.period_totals().items():
                totals[period] = totals.get(period, 0.0) + value
        return totals

    def combined(self) -> TemporalDemandMatrix:
        pairs: list[PeriodODPairDemand] = []
        for layer in self.layers:
            pairs.extend(layer.demand.pairs)
        return TemporalDemandMatrix(tuple(pairs))


def generate_purpose_layer(
    zones: tuple[DemandZone, ...],
    *,
    purpose: ReferencePurposeLayer,
    min_population: float = 40.0,
    min_distance_m: float = 50.0,
    reach_multiplier: float = 8.0,
) -> ReferenceDemandLayerResult:
    result: list[PeriodODPairDemand] = []
    scale = purpose.attraction_distance_m
    max_distance = reach_multiplier * scale

    for origin in zones:
        if origin.population < min_population:
            continue
        production = origin.population * purpose.trips_per_resource
        if production <= 0:
            continue

        candidates: list[tuple[DemandZone, float, float]] = []
        for destination in zones:
            if destination.id == origin.id:
                continue
            attraction = _purpose_attraction(destination, purpose)
            if attraction <= 0:
                continue
            distance = hypot(
                destination.centroid_x - origin.centroid_x,
                destination.centroid_y - origin.centroid_y,
            )
            if distance < min_distance_m or distance > max_distance:
                continue
            weight = attraction * exp(-distance / scale)
            if weight > 0:
                candidates.append((destination, weight, distance))

        if not candidates:
            continue

        selected = _select_candidates(
            candidates,
            purpose.max_destinations,
            purpose.attraction_distance_m,
        )
        total_weight = sum(item[1] for item in selected)
        if total_weight <= 0:
            continue

        for destination, weight, _distance in selected:
            base_daily = production * weight / total_weight
            for index, period in enumerate(REFERENCE_PERIODS):
                outbound = base_daily * purpose.outbound_shares[index]
                inbound = base_daily * purpose.return_shares[index]
                if outbound > 0:
                    result.append(
                        PeriodODPairDemand(
                            origin.id,
                            destination.id,
                            period.key,
                            outbound,
                            purpose.key,
                        )
                    )
                if inbound > 0:
                    result.append(
                        PeriodODPairDemand(
                            destination.id,
                            origin.id,
                            period.key,
                            inbound,
                            purpose.key,
                        )
                    )

    return ReferenceDemandLayerResult(
        purpose=purpose.key,
        label=purpose.label,
        demand=TemporalDemandMatrix(tuple(result)),
    )


def build_demand_layers(
    zones: tuple[DemandZone, ...],
) -> ReferenceDemandLayers:
    return ReferenceDemandLayers(
        tuple(
            generate_purpose_layer(zones, purpose=purpose)
            for purpose in REFERENCE_PURPOSE_LAYERS
        )
    )


def build_daily_demand(
    zones: tuple[DemandZone, ...],
    *,
    trip_rate: float = 0.12,
    decay: float = 0.08,
    reference_speed_kph: float = 30.0,
) -> DemandMatrix:
    if trip_rate < 0:
        raise ValueError("trip_rate cannot be negative")
    if decay <= 0:
        raise ValueError("decay must be positive")
    if reference_speed_kph <= 0:
        raise ValueError("reference_speed_kph must be positive")
    # The reference runtime has a base commuter matrix plus auxiliary
    # purpose layers. Gravity OD provides the equivalent base commuter layer
    # when the city does not have a pre-calibrated demand.json.
    commuter = gravity_od(
        zones,
        parameters=GravityParameters(
            reference_speed_kph=reference_speed_kph,
            decay=decay,
        ),
        trip_rate=trip_rate,
    )
    layers = build_demand_layers(zones)
    pairs = [
        ODPairDemand(
            pair.origin_zone_id,
            pair.destination_zone_id,
            pair.trips_per_day,
            "work",
        )
        for pair in commuter.pairs
        if pair.trips_per_day > 0
    ]
    for layer in layers.layers:
        totals: dict[tuple[str, str], float] = {}
        for pair in layer.demand.pairs:
            key = (pair.origin_zone_id, pair.destination_zone_id)
            totals[key] = totals.get(key, 0.0) + pair.trips
        pairs.extend(
            ODPairDemand(origin, destination, trips, layer.purpose)
            for (origin, destination), trips in totals.items()
            if trips > 0
        )
    return DemandMatrix(tuple(pairs))

def _select_candidates(
    candidates: list[tuple[DemandZone, float, float]],
    max_destinations: int,
    distance_scale_m: float,
) -> list[tuple[DemandZone, float, float]]:
    bands = (
        (0.0, distance_scale_m),
        (distance_scale_m, 2.5 * distance_scale_m),
        (2.5 * distance_scale_m, 6.0 * distance_scale_m),
        (6.0 * distance_scale_m, float("inf")),
    )
    per_band = max(1, round(max_destinations / len(bands)))
    selected: list[tuple[DemandZone, float, float]] = []

    for lower, upper in bands:
        band = [
            item
            for item in candidates
            if lower <= item[2] < upper
        ]
        band.sort(key=lambda item: (-item[1], item[2], item[0].id))
        selected.extend(band[:per_band])

    if len(selected) < max_destinations:
        existing = {item[0].id for item in selected}
        remainder = [
            item
            for item in candidates
            if item[0].id not in existing
        ]
        remainder.sort(key=lambda item: (-item[1], item[2], item[0].id))
        selected.extend(remainder[: max_destinations - len(selected)])

    return selected[:max_destinations]
