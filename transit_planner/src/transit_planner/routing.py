from __future__ import annotations

from dataclasses import dataclass
from heapq import heappop, heappush
from math import inf, sqrt

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


class TransitRouter:
    """Time-dependent stop router with walking links and route-level penalties.

    Stop coordinates are expected in a metric coordinate system.
    """

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
    ) -> None:
        self.network = network
        self.config = config
        self._route_neighbors_by_route: dict[str, dict[str, tuple[str, ...]]] = {}
        for route in network.routes.values():
            neighbors: dict[str, list[str]] = {sid: [] for sid in route.stop_ids}
            for a, b in zip(route.stop_ids, route.stop_ids[1:]):
                neighbors[a].append(b)
                neighbors[b].append(a)
            self._route_neighbors_by_route[route.id] = {
                sid: tuple(items) for sid, items in neighbors.items()
            }

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

            for neighbor_id, walk_time in self._walking_neighbors(stop_id):
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

            for service in self.network.services.values():
                headway = service.headway_by_period.get(period_id)
                if headway is None:
                    continue
                route = self.network.routes[service.route_id]
                neighbors = self._route_neighbors_by_route[route.id].get(stop_id, ())
                for neighbor_id in neighbors:
                    board = current_route != route.id
                    wait = (
                        headway / 2.0 * self.config.wait_weight
                        if board
                        else 0.0
                    )
                    penalty = penalties.get(route.id, 0.0) if board else 0.0
                    run = self._run_time_between(stop_id, neighbor_id, route.mode)
                    next_state = (neighbor_id, route.id)
                    candidate = cost + wait + run + penalty
                    if candidate < best.get(next_state, inf):
                        best[next_state] = candidate
                        previous[next_state] = (
                            state,
                            JourneyLeg(
                                "transit",
                                stop_id,
                                neighbor_id,
                                wait + run + penalty,
                                route.id,
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

    def _walking_neighbors(self, stop_id: str) -> tuple[tuple[str, float], ...]:
        origin = self.network.stops[stop_id]
        result: list[tuple[str, float]] = []
        for candidate in self.network.stops.values():
            if candidate.id == stop_id:
                continue
            distance_m = self._point_distance(origin, candidate)
            if distance_m <= self.config.walk_transfer_radius_m:
                result.append(
                    (
                        candidate.id,
                        distance_m / 1000.0 / self.config.walking_speed_kph * 60.0,
                    )
                )
        return tuple(result)

    def _run_time_between(self, from_id: str, to_id: str, mode: TransitMode) -> float:
        a = self.network.stops[from_id]
        b = self.network.stops[to_id]
        distance_km = self._point_distance(a, b) / 1000.0
        speed = self._SPEEDS.get(mode, self.config.default_transit_speed_kph)
        return distance_km / speed * 60.0

    @staticmethod
    def _point_distance(a: Stop, b: Stop) -> float:
        return sqrt(
            (a.location.x - b.location.x) ** 2
            + (a.location.y - b.location.y) ** 2
        )
