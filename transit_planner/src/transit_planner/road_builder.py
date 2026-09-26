from __future__ import annotations

from dataclasses import dataclass

from .data import RoadRecord
from .road import RoadEdge, RoadGraph, RoadNode


@dataclass(frozen=True, slots=True)
class RoadGraphBuildResult:
    graph: RoadGraph
    node_count: int
    edge_count: int


def build_road_graph(
    roads: tuple[RoadRecord, ...],
    *,
    coordinate_to_metre: float = 1.0,
    coordinate_precision: int = 6,
) -> RoadGraphBuildResult:
    if coordinate_to_metre <= 0:
        raise ValueError("coordinate_to_metre must be positive")

    graph = RoadGraph()
    node_by_key: dict[tuple[float, float], int] = {}
    next_node_id = 1

    def node_for(x: float, y: float) -> int:
        nonlocal next_node_id
        key = (round(x, coordinate_precision), round(y, coordinate_precision))
        existing = node_by_key.get(key)
        if existing is not None:
            return existing
        node_id = next_node_id
        next_node_id += 1
        node_by_key[key] = node_id
        graph.add_node(RoadNode(node_id, x, y))
        return node_id

    for record in roads:
        start = record.geometry.points[0]
        end = record.geometry.points[-1]
        from_node = node_for(start.x, start.y)
        to_node = node_for(end.x, end.y)
        length_m = record.geometry.length * coordinate_to_metre
        forward = RoadEdge(
            record.id,
            from_node,
            to_node,
            length_m,
            record.speed_kph,
            record.road_type,
        )
        graph.add_edge(forward)
        if not record.oneway:
            graph.add_edge(
                RoadEdge(
                    f"{record.id}:reverse",
                    to_node,
                    from_node,
                    length_m,
                    record.speed_kph,
                    record.road_type,
                )
            )

    return RoadGraphBuildResult(graph, len(graph.nodes), len(graph.edges))
