from __future__ import annotations

from dataclasses import dataclass, field
from heapq import heappop, heappush
from math import inf


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

    @property
    def travel_time_minutes(self) -> float:
        if self.length_m < 0:
            raise ValueError("Edge length cannot be negative")
        if self.speed_kph <= 0:
            raise ValueError("Edge speed must be positive")
        return self.length_m / self.speed_kph * 60.0


@dataclass(slots=True)
class RoadGraph:
    nodes: dict[int, RoadNode] = field(default_factory=dict)
    edges: dict[str, RoadEdge] = field(default_factory=dict)
    outgoing: dict[int, list[str]] = field(default_factory=dict)
    connector_nodes: dict[str, int] = field(default_factory=dict)
    prohibited_transitions: tuple[object, ...] = ()
    _restriction_index: dict[str, tuple[object, ...]] = field(default_factory=dict, init=False, repr=False)

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

    def add_prohibited_transition(self, rule: object) -> None:
        self.prohibited_transitions = (*self.prohibited_transitions, rule)
        source_segment_id = getattr(rule, "source_segment_id", None)
        if source_segment_id:
            self._restriction_index[source_segment_id] = (
                *self._restriction_index.get(source_segment_id, ()),
                rule,
            )

    def shortest_path(self, origin: int, destination: int) -> tuple[float, tuple[str, ...]]:
        if origin not in self.nodes or destination not in self.nodes:
            raise KeyError("Origin or destination node not found")
        if origin == destination:
            return 0.0, ()

        distances: dict[int, float] = {origin: 0.0}
        previous: dict[int, tuple[int, str]] = {}
        queue: list[tuple[float, int]] = [(0.0, origin)]

        while queue:
            distance, node_id = heappop(queue)
            if distance != distances.get(node_id, inf):
                continue
            if node_id == destination:
                break
            for edge_id in self.outgoing.get(node_id, ()):
                edge = self.edges[edge_id]
                candidate = distance + edge.travel_time_minutes
                if candidate < distances.get(edge.to_node, inf):
                    distances[edge.to_node] = candidate
                    previous[edge.to_node] = (node_id, edge_id)
                    heappush(queue, (candidate, edge.to_node))

        if destination not in distances:
            return inf, ()

        path: list[str] = []
        current = destination
        while current != origin:
            parent, edge_id = previous[current]
            path.append(edge_id)
            current = parent
        path.reverse()
        return distances[destination], tuple(path)
