from __future__ import annotations

from dataclasses import dataclass
from math import hypot, inf, isfinite

from .network import Network, Stop
from .reference_model import REFERENCE_MODE_PROFILES, REFERENCE_TRANSFER
from .timetable import average_connection_wait_minutes


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
    transfer_penalty_per_m_s: float = REFERENCE_TRANSFER.per_m_s
    transfer_walk_multiplier: float = REFERENCE_TRANSFER.walk_multiplier
    raptor_range_window_min: float = 30.0
    raptor_max_transfers: int = 4

    def __post_init__(self) -> None:
        if self.walking_speed_kph <= 0 or self.default_transit_speed_kph <= 0:
            raise ValueError("Speeds must be positive")
        if self.walk_transfer_radius_m < 0:
            raise ValueError("walk_transfer_radius_m cannot be negative")
        if self.wait_weight < 0 or self.transfer_penalty_min < 0:
            raise ValueError("Wait and transfer penalties cannot be negative")
        if self.transfer_penalty_per_m_s < 0:
            raise ValueError("transfer_penalty_per_m_s cannot be negative")
        if self.transfer_walk_multiplier < 0:
            raise ValueError("transfer_walk_multiplier cannot be negative")
        if self.raptor_range_window_min < 0:
            raise ValueError("raptor_range_window_min cannot be negative")
        if self.raptor_max_transfers < 0:
            raise ValueError("raptor_max_transfers cannot be negative")


@dataclass(frozen=True, slots=True)
class _RaptorPattern:
    route_id: str
    service_id: str
    stops: tuple[str, ...]
    departures: tuple[float, ...]
    segment_times: tuple[float, ...]
    direction: int


@dataclass(frozen=True, slots=True)
class _RaptorParent:
    kind: str
    previous_stop: str
    route_id: str | None
    service_id: str | None
    board_time: float
    ready_time: float
    arrival_time: float
    direction: int = 1
    board_local: int = -1
    alight_local: int = -1
    pattern_size: int = 0


class TransitRouter:
    """Schedule-aware range-rRAPTOR router.

    The transit route-choice model is timetable-first. Street topology is
    consumed upstream through Network.route_segment_run_time_min(), so this
    router never falls back to a graph shortest-path search.
    """

    def __init__(
        self,
        network: Network,
        *,
        config: RouterConfig = RouterConfig(),
    ) -> None:
        self.network = network
        self.config = config
        self._walking_neighbors_cache = self._build_walking_neighbors()
        self._pattern_cache: dict[str, tuple[_RaptorPattern, ...]] = {}

    def shortest(
        self,
        origin: Stop,
        destination: Stop,
        *,
        period_id: str,
        route_penalties: dict[str, float] | None = None,
        segment_crowding_penalties: dict[tuple[str, str, str], float] | None = None,
        service_headway_factors: dict[str, float] | None = None,
    ) -> Journey | None:
        if origin.id not in self.network.stops or destination.id not in self.network.stops:
            raise KeyError("Origin or destination stop is not in the network")
        if period_id not in self.network.periods:
            raise KeyError(period_id)
        if origin.id == destination.id:
            return Journey(origin.id, destination.id, 0.0, 0, ())

        route_penalties = route_penalties or {}
        segment_penalties = segment_crowding_penalties or {}
        headway_factors = service_headway_factors or {}
        period = self.network.periods[period_id]

        best: Journey | None = None
        best_score = inf
        first = float(period.start_minute)
        last = min(
            float(period.end_minute),
            first + self.config.raptor_range_window_min,
        )

        departure = first
        while departure <= last + 1e-9:
            candidate = self._raptor_once(
                origin=origin,
                destination=destination,
                period_id=period_id,
                departure_minute=departure,
                max_transfers=self.config.raptor_max_transfers,
                segment_penalties=segment_penalties,
                service_headway_factors=headway_factors,
            )
            if candidate is not None:
                used_routes = {
                    leg.route_id
                    for leg in candidate.legs
                    if leg.kind == "transit" and leg.route_id is not None
                }
                score = candidate.duration_min
                score += self.config.transfer_penalty_min * candidate.transfers
                score += self.config.wait_weight * sum(
                    leg.wait_min for leg in candidate.legs
                )
                score += sum(
                    max(0.0, route_penalties.get(route_id, 0.0))
                    for route_id in used_routes
                )
                if score < best_score:
                    best_score = score
                    best = candidate
            departure += 1.0

        return best

    def shortest_alternatives(
        self,
        origin: Stop,
        destination: Stop,
        *,
        period_id: str,
        max_alternatives: int = 3,
        route_penalties: dict[str, float] | None = None,
        segment_crowding_penalties: dict[tuple[str, str, str], float] | None = None,
        service_headway_factors: dict[str, float] | None = None,
        diversity_penalty_min: float = 15.0,
    ) -> tuple[Journey, ...]:
        if max_alternatives <= 0:
            return ()
        if diversity_penalty_min < 0:
            raise ValueError("diversity_penalty_min cannot be negative")

        base_penalties = dict(route_penalties or {})
        penalties = dict(base_penalties)
        results: list[Journey] = []
        seen_sequences: set[tuple[str, ...]] = set()

        for rank in range(max_alternatives):
            journey = self.shortest(
                origin,
                destination,
                period_id=period_id,
                route_penalties=penalties,
                segment_crowding_penalties=segment_crowding_penalties,
                service_headway_factors=service_headway_factors,
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

    def _raptor_once(
        self,
        *,
        origin: Stop,
        destination: Stop,
        period_id: str,
        departure_minute: float,
        max_transfers: int,
        segment_penalties: dict[tuple[str, str, str], float],
        service_headway_factors: dict[str, float],
    ) -> Journey | None:
        patterns = self._patterns(
            period_id,
            service_headway_factors,
        )
        infinity = float("inf")
        arrival: dict[str, float] = {
            stop_id: infinity for stop_id in self.network.stops
        }
        arrival[origin.id] = departure_minute

        parents: list[dict[str, _RaptorParent]] = []
        best_destination: tuple[float, int] | None = None

        for round_index in range(max_transfers + 1):
            current = dict(arrival)
            next_arrival: dict[str, float] = {
                stop_id: infinity for stop_id in self.network.stops
            }
            parent: dict[str, _RaptorParent] = {}

            for pattern in patterns:
                board_stop: str | None = None
                board_time = infinity
                ready_time = infinity

                for stop_id in pattern.stops:
                    ready = current.get(stop_id, infinity)
                    if not isfinite(ready):
                        continue
                    if not self.network.routes[pattern.route_id].is_stop_open(stop_id):
                        continue
                    scheduled = _first_departure(pattern.departures, ready)
                    if scheduled is None:
                        continue
                    if scheduled < board_time:
                        board_stop = stop_id
                        board_time = scheduled
                        ready_time = ready

                if board_stop is None:
                    continue

                board_index = pattern.stops.index(board_stop)
                running = board_time

                for local_index in range(board_index, len(pattern.stops)):
                    stop_id = pattern.stops[local_index]
                    if local_index > board_index:
                        from_stop = pattern.stops[local_index - 1]
                        to_stop = stop_id
                        running += pattern.segment_times[local_index - 1]
                        running += max(
                            0.0,
                            segment_penalties.get(
                                (pattern.route_id, from_stop, to_stop),
                                0.0,
                            ),
                        )

                    if running >= next_arrival[stop_id]:
                        continue

                    next_arrival[stop_id] = running
                    parent[stop_id] = _RaptorParent(
                        kind="transit",
                        previous_stop=board_stop,
                        route_id=pattern.route_id,
                        service_id=pattern.service_id,
                        board_time=board_time,
                        ready_time=ready_time,
                        arrival_time=running,
                        direction=pattern.direction,
                        board_local=board_index,
                        alight_local=local_index,
                        pattern_size=len(pattern.stops),
                    )

            self._apply_footpaths(next_arrival, parent)

            if isfinite(next_arrival[destination.id]):
                candidate = next_arrival[destination.id]
                if (
                    best_destination is None
                    or candidate < best_destination[0]
                ):
                    best_destination = (candidate, round_index)

            parents.append(parent)
            if not parent:
                break
            arrival = next_arrival

        if best_destination is None:
            return None

        legs = self._reconstruct(
            origin_id=origin.id,
            destination_id=destination.id,
            parents=parents,
            round_index=best_destination[1],
        )
        if legs is None:
            return None

        adjusted = _add_intermediate_dwell(self.network, legs)
        adjusted = _adjust_connection_waits(
            self.network,
            period_id,
            adjusted,
            service_headway_factors=service_headway_factors,
        )
        route_ids = [
            leg.route_id
            for leg in adjusted
            if leg.kind == "transit" and leg.route_id is not None
        ]
        transfers = sum(
            previous != current
            for previous, current in zip(route_ids, route_ids[1:])
        )

        return Journey(
            origin_stop_id=origin.id,
            destination_stop_id=destination.id,
            duration_min=sum(
                leg.duration_min
                for leg in adjusted
            ),
            transfers=transfers,
            legs=adjusted,
        )

    def _patterns(
        self,
        period_id: str,
        service_headway_factors: dict[str, float],
    ) -> tuple[_RaptorPattern, ...]:
        if not service_headway_factors:
            cached = self._pattern_cache.get(period_id)
            if cached is not None:
                return cached

        period = self.network.periods[period_id]
        patterns: list[_RaptorPattern] = []

        for service in self.network.services.values():
            base_headway = service.headway_by_period.get(period_id)
            if base_headway is None:
                continue
            multiplier = max(
                1.0,
                service_headway_factors.get(service.id, 1.0),
            )
            headway = base_headway * multiplier
            departures = _service_departures(
                period.start_minute,
                period.end_minute,
                headway,
                service.departure_offset_by_period.get(period_id, 0.0),
            )
            if not departures:
                continue

            route = self.network.routes[service.route_id]
            segment_count = len(route.segment_pairs())
            base_stops = route.stop_ids + ((route.stop_ids[0],) if route.closed else ())
            forward_times = tuple(
                self.network.route_segment_run_time_min(route, index)
                for index in range(segment_count)
            )
            patterns.append(
                _RaptorPattern(
                    route.id,
                    service.id,
                    base_stops,
                    departures,
                    forward_times,
                    1,
                )
            )

            if route.both_ways:
                reverse_stops = (
                    tuple(reversed(route.stop_ids))
                    + ((route.stop_ids[-1],) if route.closed else ())
                )
                patterns.append(
                    _RaptorPattern(
                        route.id,
                        service.id,
                        reverse_stops,
                        departures,
                        (
                            tuple(reversed(forward_times[:-1])) + (forward_times[-1],)
                            if route.closed
                            else tuple(reversed(forward_times))
                        ),
                        -1,
                    )
                )

        result = tuple(patterns)
        if not service_headway_factors:
            self._pattern_cache[period_id] = result
        return result

    def _apply_footpaths(
        self,
        arrivals: dict[str, float],
        parent: dict[str, _RaptorParent],
    ) -> None:
        if self.config.walk_transfer_radius_m <= 0:
            return

        changed = True
        while changed:
            changed = False
            for from_stop, from_arrival in tuple(arrivals.items()):
                if not isfinite(from_arrival):
                    continue
                for to_stop, walk_time in self._walking_neighbors_cache.get(from_stop, ()):
                    candidate = from_arrival + walk_time
                    if candidate >= arrivals.get(to_stop, inf):
                        continue
                    arrivals[to_stop] = candidate
                    parent[to_stop] = _RaptorParent(
                        kind="walk",
                        previous_stop=from_stop,
                        route_id=None,
                        service_id=None,
                        board_time=from_arrival,
                        ready_time=from_arrival,
                        arrival_time=candidate,
                    )
                    changed = True

    def _reconstruct(
        self,
        *,
        origin_id: str,
        destination_id: str,
        parents: list[dict[str, _RaptorParent]],
        round_index: int,
    ) -> tuple[JourneyLeg, ...] | None:
        legs_reversed: list[JourneyLeg] = []
        current = destination_id
        current_round = round_index
        guard = 0

        while current != origin_id and guard < 10000:
            guard += 1
            if current_round < 0 or current_round >= len(parents):
                return None

            record = parents[current_round].get(current)
            if record is None:
                current_round -= 1
                continue

            if record.kind == "walk":
                legs_reversed.append(
                    JourneyLeg(
                        "walk",
                        record.previous_stop,
                        current,
                        max(
                            0.0,
                            record.arrival_time - record.board_time,
                        ),
                    )
                )
                current = record.previous_stop
                continue

            route_id = record.route_id
            if route_id is None:
                return None

            route = self.network.routes[route_id]
            size = record.pattern_size
            if size < 2 or record.board_local < 0 or record.alight_local < 0:
                return None

            local_step = 1 if record.direction >= 0 else -1
            if record.direction >= 0 and record.alight_local < record.board_local:
                return None
            if record.direction < 0 and record.alight_local > record.board_local:
                return None

            local = record.board_local
            first_segment = True
            route_count = len(route.stop_ids)
            while local != record.alight_local:
                next_local = local + local_step
                if record.direction >= 0:
                    from_original = local % route_count
                    to_original = next_local % route_count
                else:
                    from_original = (route_count - 1 - local) % route_count
                    to_original = (route_count - 1 - next_local) % route_count

                from_id = route.stop_ids[from_original]
                to_id = route.stop_ids[to_original]
                segment_index = (
                    from_original
                    if record.direction >= 0
                    else (from_original - 1) % route_count
                )
                duration = self.network.route_segment_run_time_min(
                    route,
                    segment_index,
                )
                legs_reversed.append(
                    JourneyLeg(
                        "transit",
                        from_id,
                        to_id,
                        duration,
                        route_id,
                        wait_min=(
                            max(0.0, record.board_time - record.ready_time)
                            if first_segment
                            else 0.0
                        ),
                        service_id=record.service_id,
                    )
                )
                first_segment = False
                local = next_local

            current = record.previous_stop
            current_round -= 1

        if current != origin_id:
            return None

        legs_reversed.reverse()

        # Waiting is carried only by the first transit leg.
        seen_transit = False
        normalized: list[JourneyLeg] = []
        for leg in legs_reversed:
            if leg.kind == "transit":
                normalized.append(
                    JourneyLeg(
                        leg.kind,
                        leg.from_id,
                        leg.to_id,
                        leg.duration_min,
                        leg.route_id,
                        wait_min=leg.wait_min if not seen_transit else 0.0,
                        service_id=leg.service_id,
                    )
                )
                seen_transit = True
            else:
                normalized.append(leg)

        return tuple(normalized)


    def _build_walking_neighbors(
        self,
    ) -> dict[str, tuple[tuple[str, float], ...]]:
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
                duration = (
                    distance_m
                    / 1000.0
                    / self.config.walking_speed_kph
                    * 60.0
                )
                result[origin.id].append((candidate.id, duration))
                result[candidate.id].append((origin.id, duration))
        return {
            stop_id: tuple(items)
            for stop_id, items in result.items()
        }

    @staticmethod
    def _point_distance(left: Stop, right: Stop) -> float:
        return hypot(
            left.location.x - right.location.x,
            left.location.y - right.location.y,
        )


def _service_departures(
    start_minute: int,
    end_minute: int,
    headway: float,
    offset: float,
) -> tuple[float, ...]:
    if headway <= 0:
        return ()
    first = start_minute + ((offset - start_minute) % headway)
    if first >= end_minute:
        return ()
    count = int((end_minute - first - 1e-9) // headway) + 1
    return tuple(
        first + index * headway
        for index in range(max(0, count))
    )


def _first_departure(
    departures: tuple[float, ...],
    arrival_minute: float,
) -> float | None:
    left = 0
    right = len(departures)
    while left < right:
        middle = (left + right) // 2
        if departures[middle] < arrival_minute:
            left = middle + 1
        else:
            right = middle
    return departures[left] if left < len(departures) else None


def _add_intermediate_dwell(
    network: Network,
    legs: tuple[JourneyLeg, ...],
) -> tuple[JourneyLeg, ...]:
    result: list[JourneyLeg] = []
    previous_route: str | None = None

    for leg in legs:
        duration = leg.duration_min
        if (
            leg.kind == "transit"
            and leg.route_id is not None
            and previous_route == leg.route_id
            and network.routes[leg.route_id].is_stop_open(leg.from_id)
        ):
            profile = REFERENCE_MODE_PROFILES[
                network.routes[leg.route_id].mode.value
            ]
            duration += profile.dwell_s / 60.0

        result.append(
            JourneyLeg(
                leg.kind,
                leg.from_id,
                leg.to_id,
                duration,
                leg.route_id,
                leg.wait_min,
                leg.service_id,
            )
        )
        previous_route = (
            leg.route_id
            if leg.kind == "transit"
            else None
        )

    return tuple(result)



def _adjust_connection_waits(
    network: Network,
    period_id: str,
    legs: tuple[JourneyLeg, ...],
    *,
    service_headway_factors: dict[str, float] | None = None,
) -> tuple[JourneyLeg, ...]:
    if not legs:
        return legs

    result: list[JourneyLeg] = []
    factors = service_headway_factors or {}
    active_route: str | None = None
    active_service: str | None = None
    upstream_run = 0.0
    transfer_walk = 0.0

    for leg in legs:
        if leg.kind == "walk":
            result.append(leg)
            if active_route is not None:
                transfer_walk += leg.duration_min
            continue

        if leg.kind != "transit" or leg.route_id is None:
            result.append(leg)
            continue

        wait = leg.wait_min
        if active_route is not None and active_route != leg.route_id:
            upstream_service = network.services.get(active_service or "")
            downstream_service = network.services.get(leg.service_id or "")
            if upstream_service and downstream_service:
                upstream_headway = upstream_service.headway_by_period.get(period_id)
                downstream_headway = downstream_service.headway_by_period.get(period_id)
                if upstream_headway and downstream_headway:
                    upstream_profile = REFERENCE_MODE_PROFILES[
                        network.routes[upstream_service.route_id].mode.value
                    ]
                    wait = average_connection_wait_minutes(
                        upstream_headway * max(
                            1.0,
                            factors.get(upstream_service.id, 1.0),
                        ),
                        downstream_headway * max(
                            1.0,
                            factors.get(downstream_service.id, 1.0),
                        ),
                        upstream_run_time=upstream_run,
                        mode_jitter_s=upstream_profile.jitter_s,
                        walk_time_min=transfer_walk,
                    )

        result.append(
            JourneyLeg(
                leg.kind,
                leg.from_id,
                leg.to_id,
                leg.duration_min,
                leg.route_id,
                wait_min=0.0 if wait is None else wait,
                service_id=leg.service_id,
            )
        )
        active_route = leg.route_id
        active_service = leg.service_id
        upstream_run = leg.duration_min
        transfer_walk = 0.0

    return tuple(result)


