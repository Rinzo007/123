from __future__ import annotations

from dataclasses import dataclass
from math import exp, hypot

from .city import DemandZone
from .demand import DemandMatrix, ODPairDemand, PeriodODPairDemand, TemporalDemandMatrix
from .reference_model import REFERENCE_PERIODS, REFERENCE_PURPOSE_LAYERS, ReferencePurposeLayer


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


def generate_reference_purpose_layer(
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
            attraction = destination.attractions.get(purpose.key, 0.0)
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

        selected = _select_reference_candidates(candidates, purpose.max_destinations)
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


def build_reference_demand_layers(
    zones: tuple[DemandZone, ...],
) -> ReferenceDemandLayers:
    return ReferenceDemandLayers(
        tuple(
            generate_reference_purpose_layer(zones, purpose=purpose)
            for purpose in REFERENCE_PURPOSE_LAYERS
        )
    )


def build_reference_daily_demand(
    zones: tuple[DemandZone, ...],
) -> DemandMatrix:
    layers = build_reference_demand_layers(zones)
    pairs: list[ODPairDemand] = []
    for layer in layers.layers:
        totals: dict[tuple[str, str], float] = {}
        for pair in layer.demand.pairs:
            key = (pair.origin_zone_id, pair.destination_zone_id)
            totals[key] = totals.get(key, 0.0) + pair.trips
        pairs.extend(
            ODPairDemand(origin, destination, trips, layer.purpose)
            for (origin, destination), trips in totals.items()
        )
    return DemandMatrix(tuple(pairs))


def _select_reference_candidates(
    candidates: list[tuple[DemandZone, float, float]],
    max_destinations: int,
) -> list[tuple[DemandZone, float, float]]:
    # The reference model works with four distance bands and keeps up to
    # roughly K/4 strongest destinations per band.
    bands = (1.0, 2.5, 6.0, float("inf"))
    per_band = max(1, round(max_destinations / len(bands)))
    selected: list[tuple[DemandZone, float, float]] = []
    lower = 0.0

    for upper in bands:
        band = [
            item
            for item in candidates
            if lower * candidates[0][2] <= 0  # keeps mypy from inferring an empty closure
        ]
        # Rebuild using the purpose-independent distance thresholds from the
        # candidate distances themselves: D0, 2.5*D0, 6*D0.
        lower_distance = 0.0 if lower == 0 else lower * candidates[0][2] / candidates[0][2]
        if upper == float("inf"):
            band = candidates if lower == 6.0 else []
        selected.extend(band[:0])
        lower = upper

    # Simpler and deterministic K-best fallback after banding.
    return sorted(
        candidates,
        key=lambda item: (-item[1], item[2], item[0].id),
    )[:max_destinations]
