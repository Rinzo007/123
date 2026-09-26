from __future__ import annotations

from dataclasses import dataclass
from heapq import heappop, heappush
from math import inf, isfinite, sqrt

from .network import Network, Stop, TransitMode
from .timetable import average_connection_wait_minutes
from .reference_model import REFERENCE_MODE_PROFILES, REFERENCE_TRANSFER
from .road import RoadGraph


@dataclass(frozen=True, slots=True)
class JourneyLeg:
    kind: str
    from_id: str
    to_id: str
    duration_min: float
    route_id: str | None = None
    wait_min: float = 0.0
    service_id: str | None = None


@dataclass(frozen=True, slots=True)
class Journey:
    origin_stop_id: str
    destination_stop_id: str
    duration_min: float
    transfers: int
    legs: tuple[JourneyLeg, ...]


@dataclass(frozen=True, slots=True)
class RouterConfig:
    walking_speed_kph: float = 5.0
    default_transit_speed_kph: float = 20.0
    walk_transfer_radius_m: float = 500.0
    wait_weight: float = 1.0
    transfer_penalty_min: float = REFERENCE_TRANSFER.base_s / 60.0
    transfer_walk_multiplier: float = REFERENCE_TRANSFER.walk_multiplier

    def __post_init__(self) -> None:
        if self.walking_speed_kph <= 0 or self.default_transit_speed_kph <= 0:
            raise ValueError("Speeds must be positive")
        if self.walk_transfer_radius_m < 0:
            raise ValueError("walk_transfer_radius_m cannot be negative")
        if self.wait_weight < 0 or self.transfer_penalty_min < 0:
            raise ValueError("Wait and transfer penalties cannot be negative")
        if self.transfer_walk_multiplier < 0:
            raise ValueError("transfer_walk_multiplier cannot be negative")


@dataclass(frozen=True, slots=True)
class _TransitOption:
    route_id: str
    neighbor_stop_id: str
    headway: float
    departure_offset: float
    mode: TransitMode
    physical_run_time_min: float | None = None


class TransitRouter:
    """Time-dependent stop router with precomputed network adjacency."""

    _SPEEDS = {
        TransitMode(mode): profile.rows[profile.default_row].speed_kph
        for mode, profile in REFERENCE_MODE_PROFILES.items()
    }

    def __init__(
        self,
        network: Network,
        *,
        config: RouterConfig = RouterConfig(),
        road_graph: RoadGraph | None = None,
        stop_road_nodes: dict[str, int | None] | None = None,
    ) -> None:
        self.network = network
        self.config = config
        self.road_graph = road_graph
        self.stop_road_nodes = dict(stop_road_nodes or {})
        self._road_run_time_cache: dict[
            tuple[str, str, TransitMode, float | None],
            float | None,
        ] = {}
        self._walking_neighbors_cache = self._build_walking_neighbors()
        self._transit_options_by_period = self._build_transit_options()

    @classmethod
    def from_overture_network(
        cls,
        network: Network,
        overture_network,
        *,
        config: RouterConfig = RouterConfig(),
    ) -> TransitRouter:
        stop_road_nodes = {
            snap.stop_id: snap.road_node_id
            for snap in overture_network.stop_snaps
        }
        return cls(
            network,
            config=config,
            road_graph=overture_network.graph,
            stop_road_nodes=stop_road_nodes,
        )

    def shortest(
        self,
        origin: Stop,
        destination: Stop,
        *,
        period_id: str,
        route_penalties: dict[str, float] | None = None,
    ) -> Journey | None:
        if origin.id not in self.network.stops or destination.id not in self.network.stops:
            raise KeyError("Origin or destination stop is not in the network")
        if period_id not in self.network.periods:
            raise KeyError(period_id)
        if origin.id == destination.id:
            return Journey(origin.id, destination.id, 0.0, 0, ())

        options_by_stop = self._transit_options_by_period.get(period_id, {})
        penalties = route_penalties or {}
        State = tuple[str, str | None]
        start: State = (origin.id, None)
        queue: list[tuple[float, int, State]] = [(0.0, 0, start)]
        best: dict[State, float] = {start: 0.0}
        previous: dict[State, tuple[State, JourneyLeg]] = {}
        serial = 1
        target_state: State | None = None

        while queue:
            cost, _, state = heappop(queue)
            if cost != best.get(state, inf):
                continue
            stop_id, current_route = state
            if stop_id == destination.id:
                target_state = state
                break

            for neighbor_id, walk_time in self._walking_neighbors_cache.get(stop_id, ()):
                transfer_walk = current_route is not None
                effective_walk_time = (
                    walk_time * self.config.transfer_walk_multiplier
                    if transfer_walk else walk_time
                )
                next_state: State = (neighbor_id, None)
                candidate = cost + effective_walk_time
                if candidate < best.get(next_state, inf):
                    best[next_state] = candidate
                    previous[next_state] = (
                        state,
                        JourneyLeg("walk", stop_id, neighbor_id, effective_walk_time),
                    )
                    heappush(queue, (candidate, serial, next_state))
                    serial += 1

            for option in options_by_stop.get(stop_id, ()):
                board = current_route != option.route_id
                wait = 0.0
                if board:
                    period = self.network.periods[period_id]
                    wait = _scheduled_wait_minutes(
                        period_start=period.start_minute,
                        period_end=period.end_minute,
                        headway=option.headway,
                        departure_offset=option.departure_offset,
                    )
                    if wait is None:
                        continue
                penalty = (
                    self.config.transfer_penalty_min
                    + penalties.get(option.route_id, 0.0)
                    if board and current_route is not None
                    else penalties.get(option.route_id, 0.0) if board else 0.0
                )
                run = self._run_time_between(
                    stop_id,
                    option.neighbor_stop_id,
                    option.mode,
                    physical_run_time_min=option.physical_run_time_min,
                )
                next_state = (option.neighbor_stop_id, option.route_id)
                weighted_wait = wait * self.config.wait_weight
                candidate = cost + weighted_wait + run + penalty
                if candidate < best.get(next_state, inf):
                    best[next_state] = candidate
                    previous[next_state] = (
                        state,
                        JourneyLeg(
                            "transit",
                            stop_id,
                            option.neighbor_stop_id,
                            run,
                            option.route_id,
                            wait_min=wait,
                            service_id=option.service_id,
                        ),
                    )
                    heappush(queue, (candidate, serial, next_state))
                    serial += 1

        if target_state is None:
            return None

        legs: list[JourneyLeg] = []
        current = target_state
        while current != start:
            parent_leg = previous.get(current)
            if parent_leg is None:
                return None
            parent, leg = parent_leg
            legs.append(leg)
            current = parent
        legs.reverse()

        route_sequence = [leg.route_id for leg in legs if leg.kind == "transit"]
        transfers = sum(
            1
            for previous_route, route_id in zip(route_sequence, route_sequence[1:])
            if previous_route != route_id
        )
        adjusted_legs = _adjust_connection_waits(
            self.network,
            period_id,
            tuple(legs),
        )
        return Journey(
            origin_stop_id=origin.id,
            destination_stop_id=destination.id,
            duration_min=sum(leg.duration_min for leg in adjusted_legs),
            transfers=transfers,
            legs=adjusted_legs,
        )


    def shortest_alternatives(
        self,
        origin: Stop,
        destination: Stop,
        *,
        period_id: str,
        max_alternatives: int = 3,
        route_penalties: dict[str, float] | None = None,
        diversity_penalty_min: float = 15.0,
    ) -> tuple[Journey, ...]:
        """Return route-diverse journeys using deterministic route penalties.

        The first journey is the normal shortest path. Subsequent journeys are
        encouraged to avoid route IDs already used by earlier alternatives.
        This is intentionally lightweight and deterministic so it remains
        suitable for large OD matrices before a full Yen-style enumerator is
        introduced.
        """
        if max_alternatives <= 0:
            return ()
        if diversity_penalty_min < 0:
            raise ValueError("diversity_penalty_min cannot be negative")

        base_penalties = dict(route_penalties or {})
        results: list[Journey] = []
        seen_sequences: set[tuple[str, ...]] = set()
        penalties = dict(base_penalties)

        for rank in range(max_alternatives):
            journey = self.shortest(
                origin,
                destination,
                period_id=period_id,
                route_penalties=penalties,
            )
            if journey is None:
                break
            sequence = tuple(
                leg.route_id
                for leg in journey.legs
                if leg.kind == "transit" and leg.route_id is not None
            )
            if sequence in seen_sequences:
                break
            seen_sequences.add(sequence)
            results.append(journey)
            increment = diversity_penalty_min * (rank + 1)
            for route_id in sequence:
                penalties[route_id] = max(
                    penalties.get(route_id, 0.0),
                    base_penalties.get(route_id, 0.0) + increment,
                )
        return tuple(results)
\n    def _run_time_between(
        self,
        from_id: str,
        to_id: str,
        mode: TransitMode,
        *,
        physical_run_time_min: float | None = None,
    ) -> float:
        if physical_run_time_min is not None:
            return physical_run_time_min

        cache_key = (from_id, to_id, mode)
        if cache_key in self._road_run_time_cache:
            cached = self._road_run_time_cache[cache_key]
            if cached is not None:
                return cached

        if self.road_graph is not None:
            origin_node = self.stop_road_nodes.get(from_id)
            destination_node = self.stop_road_nodes.get(to_id)
            if origin_node is not None and destination_node is not None:
                _, path = self.road_graph.shortest_path(origin_node, destination_node)
                if path:
                    mode_speed = self._SPEEDS.get(
                        mode,
                        self.config.default_transit_speed_kph,
                    )
                    road_time = 0.0
                    for edge_id in path:
                        edge = self.road_graph.edges[edge_id]
                        effective_speed = min(mode_speed, edge.speed_kph)
                        road_time += edge.length_m / 1000.0 / effective_speed * 60.0
                    if isfinite(road_time) and road_time >= 0.0:
                        self._road_run_time_cache[cache_key] = road_time
                        return road_time

        a = self.network.stops[from_id]
        b = self.network.stops[to_id]
        distance_km = self._point_distance(a, b) / 1000.0
        speed = self._SPEEDS.get(
            mode,
            self.config.default_transit_speed_kph,
        )
        direct_time = distance_km / speed * 60.0
        self._road_run_time_cache[cache_key] = direct_time
        return direct_time

    @staticmethod
    def _point_distance(a: Stop, b: Stop) -> float:
        return sqrt(
            (a.location.x - b.location.x) ** 2
            + (a.location.y - b.location.y) ** 2
        )

    def _build_walking_neighbors(self) -> dict[str, tuple[tuple[str, float], ...]]:
        if self.config.walk_transfer_radius_m <= 0:
            return {stop_id: () for stop_id in self.network.stops}

        result: dict[str, list[tuple[str, float]]] = {
            stop_id: [] for stop_id in self.network.stops
        }
        stops = tuple(self.network.stops.values())
        for index, origin in enumerate(stops):
            for candidate in stops[index + 1:]:
                distance_m = self._point_distance(origin, candidate)
                if distance_m > self.config.walk_transfer_radius_m:
                    continue
                duration = distance_m / 1000.0 / self.config.walking_speed_kph * 60.0
                result[origin.id].append((candidate.id, duration))
                result[candidate.id].append((origin.id, duration))
        return {stop_id: tuple(items) for stop_id, items in result.items()}

    def _build_transit_options(
        self,
    ) -> dict[str, dict[str, tuple[_TransitOption, ...]]]:
        result: dict[str, dict[str, list[_TransitOption]]] = {
            period_id: {} for period_id in self.network.periods
        }
        for service in self.network.services.values():
            route = self.network.routes[service.route_id]
            for period_id, headway in service.headway_by_period.items():
                stop_map = result.setdefault(period_id, {})
                offset = service.departure_offset_by_period.get(period_id, 0.0)
                for index, (from_id, to_id) in enumerate(route.segment_pairs()):
                    section_id = route.track_section_for_segment(index)
                    physical_run_time_min = (
                        None
                        if section_id is None
                        else self.network.route_segment_run_time_min(route, index)
                    )
                    stop_map.setdefault(from_id, []).append(
                        _TransitOption(
                            route.id,
                            to_id,
                            headway,
                            offset,
                            route.mode,
                            physical_run_time_min,
                        )
                    )
                    if route.both_ways:
                        stop_map.setdefault(to_id, []).append(
                            _TransitOption(
                                route.id,
                                from_id,
                                headway,
                                offset,
                                route.mode,
                                physical_run_time_min,
                            )
                        )
        return {
            period_id: {
                stop_id: tuple(options)
                for stop_id, options in stop_map.items()
            }
            for period_id, stop_map in result.items()
        }


def _scheduled_wait_minutes(
    *,
    period_start: int,
    period_end: int,
    headway: float,
    departure_offset: float,
) -> float | None:
    """Среднее ожидание для статического назначения спроса.

    Без заданного времени отправления пассажира точное ожидание не определено,
    поэтому используется среднее ожидание равное половине интервала. Смещение
    отправлений не меняет среднее значение при равномерном распределении
    прибытий в течение периода.
    """
    if headway <= 0 or period_start >= period_end:
        return None
    first_departure = period_start + ((departure_offset - period_start) % headway)
    if first_departure >= period_end:
        return None
    return headway / 2.0


def _adjust_connection_waits(
    network: Network,
    period_id: str,
    legs: tuple[JourneyLeg, ...],
) -> tuple[JourneyLeg, ...]:
    if not legs:
        return legs

    result: list[JourneyLeg] = []
    active_route_id: str | None = None
    active_service_id: str | None = None
    upstream_run = 0.0
    for leg in legs:
        if leg.kind != "transit" or leg.route_id is None:
            result.append(leg)
            if leg.kind == "walk":
                active_route_id = None
                active_service_id = None
                upstream_run = 0.0
            continue

        wait = leg.wait_min
        if active_route_id is not None and active_route_id != leg.route_id:
            upstream_service = network.services.get(active_service_id or "")
            downstream_service = network.services.get(leg.service_id or "")
            if upstream_service is not None and downstream_service is not None:
                upstream_h = upstream_service.headway_by_period.get(period_id)
                downstream_h = downstream_service.headway_by_period.get(period_id)
                if upstream_h is not None and downstream_h is not None:
                    wait = average_connection_wait_minutes(
                        upstream_h,
                        downstream_h,
                        upstream_offset=upstream_service.departure_offset_by_period.get(period_id, 0.0)
                        + upstream_service.phase_by_period.get(period_id, 0.0),
                        downstream_offset=downstream_service.departure_offset_by_period.get(period_id, 0.0)
                        + downstream_service.phase_by_period.get(period_id, 0.0),
                        upstream_run_time=upstream_run,
                    ) or wait
        updated = JourneyLeg(
            leg.kind,
            leg.from_id,
            leg.to_id,
            leg.duration_min,
            leg.route_id,
            wait,
            leg.service_id,
        )
        result.append(updated)
        if active_route_id != leg.route_id:
            active_route_id = leg.route_id
            active_service_id = leg.service_id
            upstream_run = leg.duration_min
        else:
            upstream_run += leg.duration_min
    return tuple(result)
