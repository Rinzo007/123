from __future__ import annotations

from dataclasses import dataclass, field
from heapq import heappop, heappush
from math import inf

from .data import ProhibitedTransition


@dataclass(frozen=True, slots=True)
class RoadNode:
    id: int
    x: float
    y: float


@dataclass(frozen=True, slots=True)
class RoadEdge:
    id: str
    from_node: int
    to_node: int
    length_m: float
    speed_kph: float
    road_type: str = "unknown"
    segment_id: str | None = None
    from_connector_id: str | None = None
    to_connector_id: str | None = None
    direction: str | None = None
    geometry: tuple[object, ...] | None = None

    @property
    def travel_time_minutes(self) -> float:
        if self.length_m < 0:
            raise ValueError("Edge length cannot be negative")
        if self.speed_kph <= 0:
            raise ValueError("Edge speed must be positive")
        return self.length_m / 1000.0 / self.speed_kph * 60.0


@dataclass(slots=True)
class RoadGraph:
    nodes: dict[int, RoadNode] = field(default_factory=dict)
    edges: dict[str, RoadEdge] = field(default_factory=dict)
    outgoing: dict[int, list[str]] = field(default_factory=dict)
    connector_nodes: dict[str, int] = field(default_factory=dict)
    prohibited_transitions: tuple[ProhibitedTransition, ...] = ()
    _restriction_index: dict[str, tuple[ProhibitedTransition, ...]] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        for rule in self.prohibited_transitions:
            self._index_prohibited_transition(rule)

    def path_length_m(self, path: tuple[str, ...]) -> float:
        return sum(self.edges[edge_id].length_m for edge_id in path)

    def path_travel_time_minutes(self, path: tuple[str, ...]) -> float:
        return sum(self.edges[edge_id].travel_time_minutes for edge_id in path)

    def path_geometry(self, path: tuple[str, ...]) -> tuple[object, ...]:
        points: list[object] = []
        for edge_id in path:
            edge = self.edges[edge_id]
            shape = tuple(edge.geometry) if edge.geometry is not None else (
                self.nodes[edge.from_node],
                self.nodes[edge.to_node],
            )
            if points and shape:
                first = shape[0]
                previous = points[-1]
                if (
                    getattr(first, "x", None) == getattr(previous, "x", None)
                    and getattr(first, "y", None) == getattr(previous, "y", None)
                ):
                    points.extend(shape[1:])
                else:
                    points.extend(shape)
            else:
                points.extend(shape)
        return tuple(points)

    def add_node(self, node: RoadNode) -> None:
        if node.id in self.nodes:
            raise ValueError(f"Duplicate road node: {node.id}")
        self.nodes[node.id] = node
        self.outgoing.setdefault(node.id, [])

    def add_connector_node(self, connector_id: str, node: RoadNode) -> int:
        existing = self.connector_nodes.get(connector_id)
        if existing is not None:
            old = self.nodes[existing]
            if abs(old.x - node.x) > 1e-7 or abs(old.y - node.y) > 1e-7:
                raise ValueError(
                    f"Connector {connector_id} resolves to conflicting coordinates"
                )
            return existing
        self.add_node(node)
        self.connector_nodes[connector_id] = node.id
        return node.id

    def add_edge(self, edge: RoadEdge) -> None:
        if edge.id in self.edges:
            raise ValueError(f"Duplicate road edge: {edge.id}")
        if edge.from_node not in self.nodes or edge.to_node not in self.nodes:
            raise ValueError(f"Unknown endpoint for edge {edge.id}")
        if edge.length_m < 0:
            raise ValueError("Edge length cannot be negative")
        if edge.speed_kph <= 0:
            raise ValueError("Edge speed must be positive")
        self.edges[edge.id] = edge
        self.outgoing.setdefault(edge.from_node, []).append(edge.id)

    def add_prohibited_transition(self, rule: ProhibitedTransition) -> None:
        self.prohibited_transitions = (*self.prohibited_transitions, rule)
        self._index_prohibited_transition(rule)

    def _index_prohibited_transition(self, rule: ProhibitedTransition) -> None:
        source_segment_id = rule.source_segment_id
        self._restriction_index[source_segment_id] = (
            *self._restriction_index.get(source_segment_id, ()),
            rule,
        )

    def shortest_path(self, origin: int, destination: int) -> tuple[float, tuple[str, ...]]:
        if origin not in self.nodes or destination not in self.nodes:
            raise KeyError("Origin or destination node not found")
        if origin == destination:
            return 0.0, ()

        max_sequence = max(
            (len(getattr(rule, "sequence", ())) for rule in self.prohibited_transitions),
            default=0,
        )
        start_state = (origin, ())
        distances = {start_state: 0.0}
        previous = {}
        queue = [(0.0, 0, start_state)]
        serial = 1
        target_state = None

        while queue:
            distance, _, state = heappop(queue)
            if distance != distances.get(state, inf):
                continue
            node_id, history = state
            if node_id == destination:
                target_state = state
                break

            for edge_id in self.outgoing.get(node_id, ()):
                edge = self.edges[edge_id]
                if history and self._transition_prohibited(history, edge, max_sequence):
                    continue

                candidate = distance + edge.travel_time_minutes
                if max_sequence > 0:
                    next_history = (history + (edge_id,))[-(max_sequence + 1):]
                else:
                    next_history = ()
                next_state = (edge.to_node, next_history)

                if candidate < distances.get(next_state, inf):
                    distances[next_state] = candidate
                    previous[next_state] = (state, edge_id)
                    heappush(queue, (candidate, serial, next_state))
                    serial += 1

        if target_state is None:
            return inf, ()

        path = []
        current = target_state
        while current != start_state:
            parent, edge_id = previous[current]
            path.append(edge_id)
            current = parent
        path.reverse()
        return distances[target_state], tuple(path)

    def _transition_prohibited(
        self,
        history: tuple[str, ...],
        candidate: RoadEdge,
        max_sequence: int,
    ) -> bool:
        if candidate.segment_id is None or max_sequence <= 0:
            return False

        prior_edges = tuple(self.edges[edge_id] for edge_id in history)
        if not prior_edges:
            return False

        for source_offset in range(1, min(max_sequence, len(prior_edges)) + 1):
            source_edge = prior_edges[-source_offset]
            rules = self._restriction_index.get(source_edge.segment_id, ())
            for rule in rules:
                sequence = getattr(rule, "sequence", ())
                if not sequence or len(sequence) != source_offset:
                    continue

                prefix_edges = () if source_offset == 1 else prior_edges[-(source_offset - 1):]
                window = prefix_edges + (candidate,)
                source_heading = getattr(rule, "when_heading", None)
                if source_heading is not None and source_edge.direction != source_heading:
                    continue

                final_heading = getattr(rule, "final_heading", None)
                if final_heading is not None and candidate.direction != final_heading:
                    continue

                if all(
                    edge.segment_id == item.segment_id
                    and edge.from_connector_id == item.connector_id
                    for edge, item in zip(window, sequence)
                ):
                    return True
        return False
