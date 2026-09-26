from __future__ import annotations

from dataclasses import dataclass
from heapq import heappop, heappush
from math import inf, isfinite, sqrt

from .road import RoadGraph

from .network import Network, Stop, TransitMode


@dataclass(frozen=True, slots=True)
class JourneyLeg:
    kind: str
    from_id: str
    to_id: str
    duration_min: float
    route_id: str | None = None


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

    def __post_init__(self) -> None:
        if self.walking_speed_kph <= 0 or self.default_transit_speed_kph <= 0:
            raise ValueError("Speeds must be positive")
        if self.walk_transfer_radius_m < 0:
            raise ValueError("walk_transfer_radius_m cannot be negative")
        if self.wait_weight < 0:
            raise ValueError("wait_weight cannot be negative")


@dataclass(frozen=True, slots=True)
class _TransitOption:
    route_id: str
    neighbor_stop_id: str
    headway: float
    mode: TransitMode


class TransitRouter:
    """Time-dependent stop router with precomputed network adjacency."""

    _SPEEDS = {
        TransitMode.BUS: 20.0,
        TransitMode.TROLLEYBUS: 20.0,
        TransitMode.TRAM: 22.0,
        TransitMode.METRO: 35.0,
        TransitMode.REGIONAL_RAIL: 55.0,
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
        self._road_run_time_cache: dict[tuple[str, str, TransitMode], float | None] = {}
        self._walking_neighbors_cache = self._build_walking_neighbors()
        self._transit_options_by_period = self._build_transit_options()

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
                next_state: State = (neighbor_id, None)
                candidate = cost + walk_time
                if candidate < best.get(next_state, inf):
                    best[next_state] = candidate
                    previous[next_state] = (
                        state,
                        JourneyLeg("walk", stop_id, neighbor_id, walk_time),
                    )
                    heappush(queue, (candidate, serial, next_state))
                    serial += 1

            for option in options_by_stop.get(stop_id, ()):
                board = current_route != option.route_id
                wait = option.headway / 2.0 * self.config.wait_weight if board else 0.0
                penalty = penalties.get(option.route_id, 0.0) if board else 0.0
                run = self._run_time_between(
                    stop_id,
                    option.neighbor_stop_id,
                    option.mode,
                )
                next_state = (option.neighbor_stop_id, option.route_id)
                candidate = cost + wait + run + penalty
                if candidate < best.get(next_state, inf):
                    best[next_state] = candidate
                    previous[next_state] = (
                        state,
                        JourneyLeg(
                            "transit",
                            stop_id,
                            option.neighbor_stop_id,
                            wait + run + penalty,
                            option.route_id,
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
        return Journey(
            origin_stop_id=origin.id,
            destination_stop_id=destination.id,
            duration_min=best[target_state],
            transfers=transfers,
            legs=tuple(legs),
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
                for from_id, to_id in zip(route.stop_ids, route.stop_ids[1:]):
                    stop_map.setdefault(from_id, []).append(
                        _TransitOption(route.id, to_id, headway, route.mode)
                    )
                    stop_map.setdefault(to_id, []).append(
                        _TransitOption(route.id, from_id, headway, route.mode)
                    )
        return {
            period_id: {
                stop_id: tuple(options)
                for stop_id, options in stop_map.items()
            }
            for period_id, stop_map in result.items()
        }

    def _run_time_between(self, from_id: str, to_id: str, mode: TransitMode) -> float:
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
                    mode_speed = self._SPEEDS.get(mode, self.config.default_transit_speed_kph)
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
        speed = self._SPEEDS.get(mode, self.config.default_transit_speed_kph)
        direct_time = distance_km / speed * 60.0
        self._road_run_time_cache[cache_key] = direct_time
        return direct_time

    @staticmethod
    def _point_distance(a: Stop, b: Stop) -> float:
        return sqrt(
            (a.location.x - b.location.x) ** 2
            + (a.location.y - b.location.y) ** 2
        )
