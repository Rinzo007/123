from __future__ import annotations

from dataclasses import dataclass, field
from heapq import heappop, heappush
from math import inf

from .data import ProhibitedTransition
from .geo import Point


@dataclass(frozen=True, slots=True)
class StreetWalkTree:
    """One street-walking search rooted at a snapped query point."""

    root: int
    minutes: dict[int, float]
    toward_root: dict[int, tuple[int, str]]


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
    geometry: tuple[Point, ...] | None = None

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
    _incoming: dict[int, list[str]] | None = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        for rule in self.prohibited_transitions:
            self._index_prohibited_transition(rule)

    def path_length_m(self, path: tuple[str, ...]) -> float:
        return sum(self.edges[edge_id].length_m for edge_id in path)

    def path_travel_time_minutes(self, path: tuple[str, ...]) -> float:
        return sum(self.edges[edge_id].travel_time_minutes for edge_id in path)

    def path_geometry(self, path: tuple[str, ...]) -> tuple[Point, ...]:
        points: list[Point] = []
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
        self._incoming = None

    def incoming(self) -> dict[int, list[str]]:
        if self._incoming is None:
            adjacency = {node_id: [] for node_id in self.nodes}
            for edge_id, edge in self.edges.items():
                adjacency.setdefault(edge.to_node, []).append(edge_id)
            self._incoming = adjacency
        return self._incoming

    @staticmethod
    def walkable(edge: RoadEdge) -> bool:
        """Pedestrians may use every mapped segment except motorways.

        One-way designations constrain vehicles, so walkable segments are
        traversed in both directions. Motorways keep their legal restriction.
        """
        road_type = str(edge.road_type).strip().lower().replace("-", "_")
        return road_type != "motorway" and not road_type.startswith("motorway_")

    def walking_search(self, root: int, *, walking_speed_kph: float) -> StreetWalkTree:
        if root not in self.nodes:
            raise KeyError("Street-walk root node not found")
        if walking_speed_kph <= 0:
            raise ValueError("Walking speed must be positive")

        minutes = {root: 0.0}
        toward_root: dict[int, tuple[int, str]] = {}
        queue = [(0.0, 0, root)]
        serial = 1
        incoming = self.incoming()
        while queue:
            duration, _, node_id = heappop(queue)
            if duration != minutes.get(node_id, inf):
                continue
            for edge_id in self.outgoing.get(node_id, ()):
                edge = self.edges[edge_id]
                neighbor = edge.to_node
                serial = self._relax_walk_step(
                    distances=minutes,
                    toward_root=toward_root,
                    queue=queue,
                    serial=serial,
                    origin=node_id,
                    neighbor=neighbor,
                    edge=edge,
                    duration=duration,
                    walking_speed_kph=walking_speed_kph,
                )
            for edge_id in incoming.get(node_id, ()):
                edge = self.edges[edge_id]
                neighbor = edge.from_node
                serial = self._relax_walk_step(
                    distances=minutes,
                    toward_root=toward_root,
                    queue=queue,
                    serial=serial,
                    origin=node_id,
                    neighbor=neighbor,
                    edge=edge,
                    duration=duration,
                    walking_speed_kph=walking_speed_kph,
                )

        return StreetWalkTree(root, minutes, toward_root)

    @staticmethod
    def _walk_edge_minutes(edge: RoadEdge, walking_speed_kph: float) -> float:
        return max(0.0, edge.length_m) / 1000.0 / walking_speed_kph * 60.0

    def _relax_walk_step(
        self,
        *,
        distances: dict[int, float],
        toward_root: dict[int, tuple[int, str]],
        queue: list[tuple[float, int, int]],
        serial: int,
        origin: int,
        neighbor: int,
        edge: RoadEdge,
        duration: float,
        walking_speed_kph: float,
    ) -> int:
        if not self.walkable(edge):
            return serial
        candidate = duration + self._walk_edge_minutes(edge, walking_speed_kph)
        if candidate < distances.get(neighbor, inf):
            distances[neighbor] = candidate
            toward_root[neighbor] = (origin, edge.id)
            heappush(queue, (candidate, serial, neighbor))
            return serial + 1
        return serial

    def walk_path_edges(
        self,
        search: StreetWalkTree,
        target: int,
        *,
        root_first: bool,
    ) -> tuple[tuple[str, bool], ...]:
        if target not in search.minutes:
            raise KeyError(f"Road node {target} is unreachable on foot")
        oriented: list[tuple[str, bool]] = []
        current = target
        while current != search.root:
            nearer, edge_id = search.toward_root[current]
            edge = self.edges[edge_id]
            forward = edge.from_node == current and edge.to_node == nearer
            backward = edge.to_node == current and edge.from_node == nearer
            if not (forward or backward):
                raise KeyError("Street-walk ancestry does not follow a mapped edge")
            oriented.append((edge_id, backward))
            current = nearer
        if root_first:
            oriented.reverse()
        return tuple(oriented)

    def walk_path_geometry(
        self,
        oriented: tuple[tuple[str, bool], ...],
    ) -> tuple[Point, ...]:
        points: list[Point] = []
        for edge_id, reverse_edge in oriented:
            edge = self.edges[edge_id]
            shape = tuple(edge.geometry) if edge.geometry is not None else (
                self.nodes[edge.from_node],
                self.nodes[edge.to_node],
            )
            if reverse_edge:
                shape = tuple(reversed(shape))
            for point in shape:
                previous = points[-1] if points else None
                if previous is not None and previous.x == point.x and previous.y == point.y:
                    continue
                points.append(point)
        return tuple(points)

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

    def weakly_connected_components(self) -> dict[int, int]:
        """Component id per node ignoring edge direction (union-find)."""
        parent = {node_id: node_id for node_id in self.nodes}

        def find(node_id: int) -> int:
            root = node_id
            while parent[root] != root:
                root = parent[root]
            while parent[node_id] != root:
                parent[node_id], node_id = root, parent[node_id]
            return root

        for edge in self.edges.values():
            left = find(edge.from_node)
            right = find(edge.to_node)
            if left != right:
                parent[right] = left

        return {node_id: find(node_id) for node_id in self.nodes}

    def component_sizes(self) -> dict[int, int]:
        sizes: dict[int, int] = {}
        for component in self.weakly_connected_components().values():
            sizes[component] = sizes.get(component, 0) + 1
        return sizes

    def are_connected(self, origin: int, destination: int) -> bool:
        if origin not in self.nodes or destination not in self.nodes:
            raise KeyError("Origin or destination node not found")
        components = self.weakly_connected_components()
        return components[origin] == components[destination]
