from __future__ import annotations

from dataclasses import dataclass
from math import hypot, inf, isfinite

from .geo import Point
from .network import Network, Stop
from .reference_model import REFERENCE_MODE_PROFILES, REFERENCE_TRANSFER
from .road import RoadGraph
from .snap import StopSnap, snap_stops_to_road_graph
from .spatial import GridPointIndex, IndexedPoint
from .timetable import average_connection_wait_minutes


#: Pedestrian search cells used for snapping points and stops to the street graph.
ROAD_SNAP_GRID_CELL_M = 250.0


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
    road_snap_max_m: float = 150.0
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
        if self.road_snap_max_m < 0:
            raise ValueError("road_snap_max_m cannot be negative")
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
    headway: float = 0.0


@dataclass(frozen=True, slots=True)
class _RaptorParent:
    kind: str
    previous_stop: str
    previous_round: int
    route_id: str | None
    service_id: str | None
    board_time: float
    ready_time: float
    arrival_time: float
    direction: int = 1
    board_local: int = -1
    alight_local: int = -1
    pattern_size: int = 0
    boarding_wait_min: float = 0.0

class TransitRouter:
    """Schedule-aware range-rRAPTOR router.

    The transit route-choice model is timetable-first. Street topology is
    consumed upstream through Network.route_segment_run_time_min() for transit
    run times. Door-to-door searches additionally use an explicitly supplied
    road graph for pedestrian access and egress; that mode has no fallback.
    """

    def __init__(
        self,
        network: Network,
        *,
        config: RouterConfig = RouterConfig(),
        road_graph: RoadGraph | None = None,
    ) -> None:
        self.network = network
        self.config = config
        self.road_graph = road_graph
        self._walking_neighbors_cache = self._build_walking_neighbors()
        self._pattern_cache: dict[str, tuple[_RaptorPattern, ...]] = {}
        self._road_node_index: GridPointIndex | None = None
        self._stop_road_snaps: dict[str, StopSnap] = {}
        if road_graph is not None:
            node_index = GridPointIndex(cell_size=ROAD_SNAP_GRID_CELL_M)
            for node in road_graph.nodes.values():
                node_index.insert(IndexedPoint(node.id, node.x, node.y))
            self._road_node_index = node_index
            snaps = snap_stops_to_road_graph(
                tuple(network.stops.values()),
                road_graph,
                cell_size=ROAD_SNAP_GRID_CELL_M,
                max_distance=config.road_snap_max_m,
            )
            self._stop_road_snaps = {snap.stop_id: snap for snap in snaps}

    def _require_street_graph(self) -> tuple[RoadGraph, GridPointIndex]:
        if self.road_graph is None or self._road_node_index is None:
            raise RuntimeError(
                "Door-to-door routing needs TransitRouter(..., road_graph=...); "
                "fallback to stop-to-stop routing is not allowed here"
            )
        return self.road_graph, self._road_node_index

    def _snap_street_point(self, point: Point, label: str) -> tuple[int, float]:
        graph, node_index = self._require_street_graph()
        nearest = node_index.nearest(
            point.x,
            point.y,
            max_radius=self.config.road_snap_max_m,
        )
        if nearest is None:
            raise ValueError(
                f"{label} is farther than {self.config.road_snap_max_m:g} m "
                "from the street graph"
            )
        return nearest.id, hypot(nearest.x - point.x, nearest.y - point.y)

    def _walk_minutes(self, distance_m: float) -> float:
        return max(0.0, distance_m) / 1000.0 / self.config.walking_speed_kph * 60.0

    def _street_door_minutes(
        self,
        origin: Point,
        destination: Point,
    ) -> tuple[dict[str, float], dict[str, float]]:
        """Closed-door walk times from and to every reachable transit stop.

        The shared pedestrian catchment is walk_transfer_radius_m. It is a
        street-path budget, not a straight-line radius: point-to-node and
        node-to-stop connector hops are part of the distance.
        """
        graph, _ = self._require_street_graph()
        origin_node, origin_offset_m = self._snap_street_point(origin, "Origin")
        destination_node, destination_offset_m = self._snap_street_point(
            destination, "Destination"
        )
        origin_search = graph.walking_search(
            origin_node, walking_speed_kph=self.config.walking_speed_kph
        )
        destination_search = graph.walking_search(
            destination_node, walking_speed_kph=self.config.walking_speed_kph
        )
        limit_m = self.config.walk_transfer_radius_m
        access: dict[str, float] = {}
        egress: dict[str, float] = {}
        for stop_id, snap in self._stop_road_snaps.items():
            if snap.road_node_id is None:
                continue
            stop_offset_m = snap.distance
            origin_graph_m = origin_search.minutes.get(snap.road_node_id)
            if origin_graph_m is not None:
                access_m = (
                    origin_offset_m
                    + origin_graph_m * self.config.walking_speed_kph / 60.0 * 1000.0
                    + stop_offset_m
                )
                if access_m <= limit_m or abs(access_m - limit_m) <= 1e-9:
                    access[stop_id] = self._walk_minutes(access_m)
            destination_graph_m = destination_search.minutes.get(snap.road_node_id)
            if destination_graph_m is not None:
                egress_m = (
                    stop_offset_m
                    + destination_graph_m * self.config.walking_speed_kph / 60.0 * 1000.0
                    + destination_offset_m
                )
                if egress_m <= limit_m or abs(egress_m - limit_m) <= 1e-9:
                    egress[stop_id] = self._walk_minutes(egress_m)

        if not access:
            raise ValueError(
                "No transit stop is reachable on foot from the origin within "
                f"{limit_m:g} m"
            )
        if not egress:
            raise ValueError(
                "No transit stop can reach the destination on foot within "
                f"{limit_m:g} m"
            )
        return access, egress

    def _score_candidate(self, candidate: Journey, route_penalties: dict[str, float]) -> float:
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
        return score

    def shortest(
        self,
        origin: Stop,
        destination: Stop,
        *,
        period_id: str,
        route_penalties: dict[str, float] | None = None,
        segment_crowding_penalties: dict[tuple[str, str, str], float] | None = None,
        service_headway_factors: dict[str, float] | None = None,
        banned_route_ids: frozenset[str] = frozenset(),
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
                banned_route_ids=banned_route_ids,
            )
            if candidate is not None:
                score = self._score_candidate(candidate, route_penalties)
                if score < best_score:
                    best_score = score
                    best = candidate
            departure += 1.0

        return best

    def shortest_from_points(
        self,
        origin: Point,
        destination: Point,
        *,
        origin_id: str = "origin",
        destination_id: str = "destination",
        period_id: str,
        route_penalties: dict[str, float] | None = None,
        segment_crowding_penalties: dict[tuple[str, str, str], float] | None = None,
        service_headway_factors: dict[str, float] | None = None,
        banned_route_ids: frozenset[str] = frozenset(),
    ) -> Journey | None:
        """Door-to-door transit journey using street-graph pedestrian access.

        origin_stop_id/destination_stop_id in the result are the boarding and
        alighting stops. The query points are the endpoints of the leading
        access leg and trailing egress leg; all candidate stops within the
        shared pedestrian catchment are searched.
        """
        if not origin_id.strip() or not destination_id.strip():
            raise ValueError("Door-to-door endpoint labels cannot be empty")
        if origin_id in self.network.stops or destination_id in self.network.stops:
            raise ValueError("Door-to-door endpoint labels must not be stop ids")
        if origin_id == destination_id:
            raise ValueError("Door-to-door endpoint labels must be distinct")
        if period_id not in self.network.periods:
            raise KeyError(period_id)

        access_minutes, egress_minutes = self._street_door_minutes(
            origin, destination
        )
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
                origin=None,
                destination=None,
                access_minutes=access_minutes,
                access_origin_id=origin_id,
                egress_minutes=egress_minutes,
                egress_destination_id=destination_id,
                period_id=period_id,
                departure_minute=departure,
                max_transfers=self.config.raptor_max_transfers,
                segment_penalties=segment_penalties,
                service_headway_factors=headway_factors,
                banned_route_ids=banned_route_ids,
            )
            if candidate is not None:
                score = self._score_candidate(candidate, route_penalties)
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
        seen_route_ids: set[str] = set()

        for rank in range(max_alternatives):
            journey = self.shortest(
                origin,
                destination,
                period_id=period_id,
                route_penalties=penalties,
                segment_crowding_penalties=segment_crowding_penalties,
                service_headway_factors=service_headway_factors,
                banned_route_ids=frozenset(seen_route_ids),
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
            seen_route_ids.update(sequence)
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
        origin: Stop | None = None,
        destination: Stop | None = None,
        access_minutes: dict[str, float] | None = None,
        access_origin_id: str | None = None,
        egress_minutes: dict[str, float] | None = None,
        egress_destination_id: str | None = None,
        period_id: str,
        departure_minute: float,
        max_transfers: int,
        segment_penalties: dict[tuple[str, str, str], float],
        service_headway_factors: dict[str, float],
        banned_route_ids: frozenset[str] = frozenset(),
    ) -> Journey | None:
        door_to_door = (
            access_minutes is not None
            or egress_minutes is not None
            or access_origin_id is not None
            or egress_destination_id is not None
        )
        if door_to_door:
            if origin is not None or destination is not None:
                raise ValueError("Point searches use access/egress maps, not Stop objects")
            if (
                access_minutes is None
                or egress_minutes is None
                or access_origin_id is None
                or egress_destination_id is None
            ):
                raise ValueError("Point searches need complete access/egress inputs")
            source_id = access_origin_id
        else:
            if origin is None or destination is None:
                raise ValueError("Stop searches need origin and destination stops")
            source_id = origin.id
        patterns = self._patterns(
            period_id,
            service_headway_factors,
        )
        if banned_route_ids:
            patterns = tuple(
                pattern
                for pattern in patterns
                if pattern.route_id not in banned_route_ids
            )
        infinity = float("inf")
        arrival_by_round: list[dict[str, float]] = [
            {stop_id: infinity for stop_id in self.network.stops}
            for _ in range(max_transfers + 1)
        ]
        arrival_by_round[0][source_id] = departure_minute
        parents: list[dict[str, _RaptorParent]] = [{} for _ in range(max_transfers + 1)]
        if door_to_door:
            assert access_minutes is not None
            for stop_id, walk_min in access_minutes.items():
                if not isfinite(walk_min):
                    continue
                arrival = departure_minute + walk_min
                if arrival < arrival_by_round[0].get(stop_id, infinity):
                    arrival_by_round[0][stop_id] = arrival
                    parents[0][stop_id] = _RaptorParent(
                        kind="access",
                        previous_stop=source_id,
                        previous_round=0,
                        route_id=None,
                        service_id=None,
                        board_time=departure_minute,
                        ready_time=departure_minute,
                        arrival_time=arrival,
                    )
            # Pre-boarding stop-to-stop walks are part of round zero and do not
            # consume a transit round.
            self._apply_footpaths(arrival_by_round[0], parents[0], 0)
        best_destination: tuple[float, int, str] | None = None
        best_destination_arrival = infinity

        for round_index in range(1, max_transfers + 1):
            current = arrival_by_round[round_index - 1]
            # Stage 1: carry forward labels and provenance (RAPTOR Algorithm 1).
            next_arrival: dict[str, float] = dict(current)
            next_parents: dict[str, _RaptorParent] = dict(parents[round_index - 1])
            improved = False

            for pattern in patterns:
                route = self.network.routes[pattern.route_id]
                best_key = infinity
                board_stop: str | None = None
                board_local = -1
                board_time = infinity
                ready_time = infinity
                cumulative = 0.0
                stop_count = len(pattern.stops)

                # Stage 2: single pass per route; boarding choice maximizes
                # earliest arrival downstream, i.e. minimizes departure -
                # cumulative runtime (paper Section 3, trip update).
                for local_index, stop_id in enumerate(pattern.stops):
                    ready = current.get(stop_id, infinity)
                    if isfinite(ready) and route.is_stop_open(stop_id):
                        scheduled = _first_departure(pattern.departures, ready)
                        if scheduled is not None and scheduled - cumulative < best_key:
                            best_key = scheduled - cumulative
                            board_stop = stop_id
                            board_local = local_index
                            board_time = scheduled
                            ready_time = ready

                    if board_stop is not None:
                        arrival = best_key + cumulative
                        if arrival < next_arrival[stop_id]:
                            next_arrival[stop_id] = arrival
                            next_parents[stop_id] = _RaptorParent(
                                kind="transit",
                                previous_stop=board_stop,
                                previous_round=round_index - 1,
                                route_id=pattern.route_id,
                                service_id=pattern.service_id,
                                board_time=board_time,
                                ready_time=ready_time,
                                arrival_time=arrival,
                                direction=pattern.direction,
                                board_local=board_local,
                                alight_local=local_index,
                                pattern_size=stop_count,
                                boarding_wait_min=pattern.headway / 2.0,
                            )
                            improved = True

                    if local_index < stop_count - 1:
                        cumulative += pattern.segment_times[local_index]
                        cumulative += max(
                            0.0,
                            segment_penalties.get(
                                (
                                    pattern.route_id,
                                    stop_id,
                                    pattern.stops[local_index + 1],
                                ),
                                0.0,
                            ),
                        )

            # Stage 3: foot-paths, walking does not consume a round.
            if self._apply_footpaths(next_arrival, next_parents, round_index):
                improved = True

            arrival_by_round[round_index] = next_arrival
            parents[round_index] = next_parents

            if door_to_door:
                assert egress_minutes is not None
                egress_targets: dict[str, float] = egress_minutes
            else:
                assert destination is not None
                egress_targets = {destination.id: 0.0}
            for target_id, egress_walk in egress_targets.items():
                if not isfinite(egress_walk):
                    continue
                candidate_arrival = next_arrival.get(target_id, infinity)
                if not isfinite(candidate_arrival):
                    continue
                door_arrival = candidate_arrival + egress_walk
                if door_arrival < best_destination_arrival:
                    best_destination_arrival = door_arrival
                    best_destination = (door_arrival, round_index, target_id)

            if not improved:
                break

        if best_destination is None:
            return None

        legs = self._reconstruct(
            origin_id=source_id,
            destination_id=best_destination[2],
            parents=parents,
            round_index=best_destination[1],
            segment_penalties=segment_penalties,
        )
        if legs is None:
            return None

        adjusted = _add_intermediate_dwell(self.network, legs)
        adjusted = _adjust_connection_waits(
            network=self.network,
            period_id=period_id,
            legs=adjusted,
            service_headway_factors=service_headway_factors,
        )
        if door_to_door:
            assert egress_minutes is not None
            assert egress_destination_id is not None
            egress_walk = egress_minutes.get(legs[-1].to_id)
            if egress_walk is None:
                return None
            completed = (
                *adjusted,
                JourneyLeg(
                    "egress",
                    legs[-1].to_id,
                    egress_destination_id,
                    egress_walk,
                ),
            )
        else:
            completed = adjusted
        transit_legs = [
            leg for leg in completed if leg.kind == "transit"
        ]
        if door_to_door:
            assert access_origin_id is not None
            assert egress_destination_id is not None
            if transit_legs:
                journey_origin_id = transit_legs[0].from_id
                journey_destination_id = transit_legs[-1].to_id
            else:
                journey_origin_id = access_origin_id
                journey_destination_id = egress_destination_id
        else:
            assert origin is not None
            assert destination is not None
            journey_origin_id = origin.id
            journey_destination_id = destination.id
        route_ids = [
            leg.route_id
            for leg in completed
            if leg.kind == "transit" and leg.route_id is not None
        ]
        transfers = sum(
            previous != current
            for previous, current in zip(route_ids, route_ids[1:])
        )

        return Journey(
            origin_stop_id=journey_origin_id,
            destination_stop_id=journey_destination_id,
            duration_min=sum(
                leg.duration_min
                for leg in completed
            ),
            transfers=transfers,
            legs=completed,
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
                    headway,
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
                        headway,
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
        round_index: int,
    ) -> bool:
        if self.config.walk_transfer_radius_m <= 0:
            return False

        changed_any = False
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
                        previous_round=round_index,
                        route_id=None,
                        service_id=None,
                        board_time=from_arrival,
                        ready_time=from_arrival,
                        arrival_time=candidate,
                    )
                    changed = True
                    changed_any = True
        return changed_any

    def _reconstruct(
        self,
        *,
        origin_id: str,
        destination_id: str,
        parents: list[dict[str, _RaptorParent]],
        round_index: int,
        segment_penalties: dict[tuple[str, str, str], float] | None = None,
    ) -> tuple[JourneyLeg, ...] | None:
        penalties = segment_penalties or {}
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
                return None

            if record.kind == "walk" or record.kind == "access":
                legs_reversed.append(
                    JourneyLeg(
                        record.kind,
                        record.previous_stop,
                        current,
                        max(
                            0.0,
                            record.arrival_time - record.board_time,
                        ),
                    )
                )
                current = record.previous_stop
                # Walking does not consume a round: same label layer.
                continue

            route_id = record.route_id
            if route_id is None:
                return None

            route = self.network.routes[route_id]
            size = record.pattern_size
            if size < 2 or record.board_local < 0 or record.alight_local < 0:
                return None
            if record.alight_local < record.board_local:
                return None

            route_count = len(route.stop_ids)
            local = record.board_local
            first_segment = True
            while local != record.alight_local:
                next_local = local + 1
                if record.direction >= 0:
                    from_original = local % route_count
                    to_original = next_local % route_count
                    segment_index = from_original
                else:
                    from_original = (route_count - 1 - local) % route_count
                    to_original = (route_count - 1 - next_local) % route_count
                    segment_index = to_original

                from_id = route.stop_ids[from_original]
                to_id = route.stop_ids[to_original]
                duration = self.network.route_segment_run_time_min(
                    route,
                    segment_index,
                ) + max(0.0, penalties.get((route_id, from_id, to_id), 0.0))
                legs_reversed.append(
                    JourneyLeg(
                        "transit",
                        from_id,
                        to_id,
                        duration,
                        route_id,
                        wait_min=record.boarding_wait_min if first_segment else 0.0,
                        service_id=record.service_id,
                    )
                )
                first_segment = False
                local = next_local

            current = record.previous_stop
            current_round = record.previous_round

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


