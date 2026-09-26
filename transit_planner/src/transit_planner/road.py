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

    def add_node(self, node: RoadNode) -> None:
        if node.id in self.nodes:
            raise ValueError(f"Duplicate road node: {node.id}")
        self.nodes[node.id] = node
        self.outgoing.setdefault(node.id, [])

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
