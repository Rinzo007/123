from __future__ import annotations

from dataclasses import dataclass
from math import asin, cos, exp, floor, hypot, radians, sin, sqrt

from .city import DemandZone
from .geo import Point
from .places import CityPlace, PlacePurposeMapper
from .projection import project_local_point_wgs84
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
    od_pairs: tuple[tuple[str, str, float, float], ...] = ()

    @property
    def total_trips(self) -> float:
        return self.demand.total_trips


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
    od_pairs: list[tuple[str, str, float, float]] = []
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

        for destination, weight, distance in selected:
            base_daily = production * weight / total_weight
            base_time_s = max(120.0, round(distance * 1.35 / 7.5 + 240.0))
            od_pairs.append((origin.id, destination.id, base_daily, base_time_s))
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
        od_pairs=tuple(od_pairs),
    )




def _js_round(value: float) -> int:
    return int(floor(value + 0.5))


def _haversine_m(a_lon: float, a_lat: float, b_lon: float, b_lat: float) -> float:
    radius = 6_371_000.0
    lat1 = radians(a_lat)
    lat2 = radians(b_lat)
    dlat = radians(b_lat - a_lat)
    dlon = radians(b_lon - a_lon)
    h = sin(dlat / 2.0) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2.0) ** 2
    return 2.0 * radius * asin(min(1.0, sqrt(max(0.0, h))))


def _reference_generator_weights(
    zones: tuple[DemandZone, ...],
    places: tuple[CityPlace, ...],
    *,
    purpose: str,
    origin_lon: float,
    origin_lat: float,
    mapper: PlacePurposeMapper | None = None,
) -> dict[int, float]:
    mapper = mapper or PlacePurposeMapper()
    demand_points = [
        project_local_point_wgs84(
            Point(zone.centroid_x, zone.centroid_y),
            origin_lon=origin_lon,
            origin_lat=origin_lat,
        )
        for zone in zones
    ]
    point_cells: dict[tuple[int, int], list[int]] = {}
    for index, point in enumerate(demand_points):
        key = (
            floor(point.x / 111_320.0 / 0.01),
            floor(point.y / 111_000.0 / (0.01 * 0.62)),
        )
        point_cells.setdefault(key, []).append(index)

    generators: dict[tuple[int, int], tuple[float, int, float]] = {}
    for place in places:
        place_purpose = mapper.purpose_for(place)
        if place_purpose is None or place_purpose.value != purpose:
            continue
        cell = (
            floor(place.location.x / 0.01),
            floor(place.location.y / (0.01 * 0.62)),
        )
        best_index = -1
        best_distance = 900.0
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for index in point_cells.get((cell[0] + dx, cell[1] + dy), ()):
                    point = project_local_point_wgs84(
                        Point(zones[index].centroid_x, zones[index].centroid_y),
                        origin_lon=origin_lon,
                        origin_lat=origin_lat,
                    )
                    distance = _haversine_m(
                        place.location.x,
                        place.location.y,
                        point.x,
                        point.y,
                    )
                    if distance < best_distance:
                        best_distance = distance
                        best_index = index
        if best_index < 0:
            continue
        current = generators.get(cell)
        importance = max(0.0, float(place.importance))
        if current is None:
            generators[cell] = (importance, best_index, importance)
        else:
            total, representative, best_importance = current
            generators[cell] = (
                total + importance,
                best_index if importance > best_importance else representative,
                max(best_importance, importance),
            )

    return {
        representative: total
        for total, representative, _best_importance in generators.values()
        if total > 0
    }


def generate_reference_purpose_layer(
    zones: tuple[DemandZone, ...],
    places: tuple[CityPlace, ...],
    *,
    purpose: ReferencePurposeLayer,
    origin_lon: float,
    origin_lat: float,
) -> ReferenceDemandLayerResult:
    points = [
        project_local_point_wgs84(
            Point(zone.centroid_x, zone.centroid_y),
            origin_lon=origin_lon,
            origin_lat=origin_lat,
        )
        for zone in zones
    ]
    attractions = _reference_generator_weights(
        zones,
        places,
        purpose=purpose.key,
        origin_lon=origin_lon,
        origin_lat=origin_lat,
    )
    bands = (
        0.0,
        purpose.attraction_distance_m,
        2.5 * purpose.attraction_distance_m,
        6.0 * purpose.attraction_distance_m,
        float("inf"),
    )
    max_distance = 8.0 * purpose.attraction_distance_m
    per_band = max(1, _js_round(purpose.max_destinations / 4.0))
    result: list[tuple[str, str, float, float]] = []

    for origin_index, origin_zone in enumerate(zones):
        if origin_zone.population < 40.0:
            continue
        production = origin_zone.population * purpose.trips_per_resource
        if production <= 0:
            continue

        candidates: list[list[tuple[int, float, float]]] = [[], [], [], []]
        band_weight = [0.0] * 4
        total_weight = 0.0
        origin = points[origin_index]
        for destination_index, attraction in attractions.items():
            if destination_index == origin_index:
                continue
            destination = points[destination_index]
            distance = _haversine_m(origin.x, origin.y, destination.x, destination.y)
            if distance < 50.0 or distance > max_distance:
                continue
            weight = attraction * exp(-distance / purpose.attraction_distance_m)
            if weight <= 0:
                continue
            band = 0
            while band < 3 and distance >= bands[band + 1]:
                band += 1
            candidates[band].append((destination_index, weight, distance))
            band_weight[band] += weight
            total_weight += weight

        if total_weight <= 0:
            continue

        rounding_carry = 0.0
        for band in range(4):
            if band_weight[band] <= 0:
                continue
            selected = sorted(
                candidates[band],
                key=lambda item: (-item[1], item[2], item[0]),
            )[:per_band]
            selected_weight = sum(item[1] for item in selected)
            if selected_weight <= 0:
                continue
            band_trips = production * band_weight[band] / total_weight
            for destination_index, weight, distance in selected:
                exact = band_trips * weight / selected_weight + rounding_carry
                trips = _js_round(exact)
                rounding_carry = exact - trips
                if trips > 0:
                    result.append((
                        origin_zone.id,
                        zones[destination_index].id,
                        float(trips),
                        float(_js_round(distance * 1.35 / 7.5 + 240.0)),
                    ))

    return ReferenceDemandLayerResult(
        purpose=purpose.key,
        label=purpose.label,
        demand=TemporalDemandMatrix(
            tuple(
                PeriodODPairDemand(
                    origin_id,
                    destination_id,
                    period.key,
                    trips * (
                        purpose.outbound_shares[index]
                        + purpose.return_shares[index]
                    ) / 2.0,
                    purpose.key,
                    base_s / 60.0,
                )
                for origin_id, destination_id, trips, base_s in result
                for index, period in enumerate(REFERENCE_PERIODS)
                if purpose.outbound_shares[index] + purpose.return_shares[index] > 0
            )
        ),
        od_pairs=tuple(result),
    )


def build_reference_demand_layers(
    zones: tuple[DemandZone, ...],
    places: tuple[CityPlace, ...],
    *,
    origin_lon: float,
    origin_lat: float,
) -> ReferenceDemandLayers:
    return ReferenceDemandLayers(tuple(
        generate_reference_purpose_layer(
            zones,
            places,
            purpose=purpose,
            origin_lon=origin_lon,
            origin_lat=origin_lat,
        )
        for purpose in REFERENCE_PURPOSE_LAYERS
    ))


def build_demand_layers(
    zones: tuple[DemandZone, ...],
) -> ReferenceDemandLayers:
    return ReferenceDemandLayers(
        tuple(
            generate_purpose_layer(zones, purpose=purpose)
            for purpose in REFERENCE_PURPOSE_LAYERS
        )
    )


def build_temporal_demand(
    zones: tuple[DemandZone, ...],
    *,
    trip_rate: float = 0.12,
    decay: float = 0.08,
    speed_kph: float = 30.0,
) -> TemporalDemandMatrix:
    commuter = gravity_od(
        zones,
        parameters=GravityParameters(speed_kph=speed_kph, decay=decay),
        trip_rate=trip_rate,
    )
    rows: list[PeriodODPairDemand] = []

    # Gravity OD is the base home-to-destination commuter matrix.
    # Preserve total daily commuter trips while distributing the matrix over
    # the canonical five operating periods.
    commuter_period_share = tuple(
        (period.outbound_share + period.return_share) / 2.0
        for period in REFERENCE_PERIODS
    )
    for pair in commuter.pairs:
        for period, share in zip(REFERENCE_PERIODS, commuter_period_share):
            trips = pair.trips_per_day * share
            if trips > 0:
                rows.append(
                    PeriodODPairDemand(
                        pair.origin_zone_id,
                        pair.destination_zone_id,
                        period.key,
                        trips,
                        "work",
                        pair.base_time_min,
                    )
                )

    return TemporalDemandMatrix(tuple(rows) + build_demand_layers(zones).combined().pairs)

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
            speed_kph=reference_speed_kph,
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
            pair.base_time_min,
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
