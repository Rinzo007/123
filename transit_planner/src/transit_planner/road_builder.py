from __future__ import annotations

from dataclasses import dataclass

from .data import ConnectorRef, RoadRecord
from .geo import Point
from .road import RoadEdge, RoadGraph, RoadNode


@dataclass(frozen=True, slots=True)
class RoadGraphBuildResult:
    graph: RoadGraph
    node_count: int
    edge_count: int
    connector_count: int = 0


def build_road_graph(
    roads: tuple[RoadRecord, ...],
    *,
    coordinate_to_metre: float = 1.0,
    coordinate_precision: int = 6,
) -> RoadGraphBuildResult:
    """Build a graph from endpoint geometry.

    Kept as a generic fallback for sources that have no explicit topology.
    Overture data should use build_topological_road_graph().
    """
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
        length_m = _length_m(record, coordinate_to_metre)
        if record.forward_allowed:
            graph.add_edge(
                RoadEdge(
                    record.id,
                    from_node,
                    to_node,
                    length_m,
                    record.speed_kph,
                    record.road_type,
                    record.id,
                    None,
                    None,
                    "forward",
                    tuple(record.geometry.points),
                )
            )
        if record.backward_allowed and not record.oneway:
            graph.add_edge(
                RoadEdge(
                    f"{record.id}:reverse",
                    to_node,
                    from_node,
                    length_m,
                    record.speed_kph,
                    record.road_type,
                    record.id,
                    None,
                    None,
                    "backward",
                )
            )

    return RoadGraphBuildResult(
        graph,
        len(graph.nodes),
        len(graph.edges),
        len(graph.connector_nodes),
    )


def build_topological_road_graph(
    roads: tuple[RoadRecord, ...],
    *,
    coordinate_to_metre: float = 1.0,
    coordinate_precision: int = 6,
) -> RoadGraphBuildResult:
    """Build a graph using Overture connector_id + linear-reference topology.

    A shared connector_id always maps to one graph node. A segment is split
    between consecutive connector references. Geometry overlap or coincident
    coordinates are not treated as a connection unless the connector IDs agree.
    """
    if coordinate_to_metre <= 0:
        raise ValueError("coordinate_to_metre must be positive")

    graph = RoadGraph()
    next_node_id = 1
    endpoint_nodes: dict[tuple[float, float], int] = {}

    def endpoint_node(point_x: float, point_y: float) -> int:
        nonlocal next_node_id
        key = (round(point_x, coordinate_precision), round(point_y, coordinate_precision))
        existing = endpoint_nodes.get(key)
        if existing is not None:
            return existing
        node_id = next_node_id
        next_node_id += 1
        endpoint_nodes[key] = node_id
        graph.add_node(RoadNode(node_id, point_x, point_y))
        return node_id

    for record in roads:
        refs = _normalized_refs(record.connectors)
        if len(refs) < 2:
            _add_fallback_segment(
                graph,
                record,
                endpoint_node,
                coordinate_to_metre,
            )
            continue

        points = record.geometry.points
        for ref in refs:
            point = _interpolate_fraction(points, ref.at)
            connector_node_id = graph.connector_nodes.get(ref.connector_id)
            node = RoadNode(
                connector_node_id if connector_node_id is not None else next_node_id,
                point.x,
                point.y,
            )
            if connector_node_id is None:
                next_node_id += 1
                graph.add_connector_node(ref.connector_id, node)
            else:
                graph.add_connector_node(ref.connector_id, node)

        for rule in record.prohibited_transitions:
            graph.add_prohibited_transition(rule)

        for left_ref, right_ref in zip(refs, refs[1:]):
            left_node = graph.connector_nodes[left_ref.connector_id]
            right_node = graph.connector_nodes[right_ref.connector_id]
            at_delta = right_ref.at - left_ref.at
            if at_delta <= 0:
                raise ValueError(
                    f"Connector references on segment {record.id} are not strictly increasing"
                )

            length_m = _length_m(record, coordinate_to_metre) * at_delta
            edge_id = f"{record.id}:{left_ref.connector_id}:{right_ref.connector_id}"
            if record.forward_allowed:
                graph.add_edge(
                    RoadEdge(
                        edge_id,
                        left_node,
                        right_node,
                        length_m,
                        record.speed_kph,
                        record.road_type,
                        record.id,
                        left_ref.connector_id,
                        right_ref.connector_id,
                        "forward",
                    )
                )
            if record.backward_allowed and not record.oneway:
                graph.add_edge(
                    RoadEdge(
                        f"{edge_id}:reverse",
                        right_node,
                        left_node,
                        length_m,
                        record.speed_kph,
                        record.road_type,
                        record.id,
                        right_ref.connector_id,
                        left_ref.connector_id,
                        "backward",
                    )
                )


    return RoadGraphBuildResult(
        graph,
        len(graph.nodes),
        len(graph.edges),
        len(graph.connector_nodes),
    )


def _length_m(record: RoadRecord, coordinate_to_metre: float) -> float:
    if record.length_m is not None:
        return record.length_m
    return record.geometry.length * coordinate_to_metre


def _normalized_refs(refs: tuple[ConnectorRef, ...]) -> tuple[ConnectorRef, ...]:
    return tuple(sorted(refs, key=lambda ref: ref.at))


def _add_fallback_segment(
    graph: RoadGraph,
    record: RoadRecord,
    endpoint_node,
    coordinate_to_metre: float,
) -> None:
    start = record.geometry.points[0]
    end = record.geometry.points[-1]
    from_node = endpoint_node(start.x, start.y)
    to_node = endpoint_node(end.x, end.y)
    length_m = _length_m(record, coordinate_to_metre)
    if record.forward_allowed:
        graph.add_edge(
            RoadEdge(
                record.id,
                from_node,
                to_node,
                length_m,
                record.speed_kph,
                record.road_type,
                record.id,
                None,
                None,
                "forward",
            )
        )
    if record.backward_allowed and not record.oneway:
        graph.add_edge(
            RoadEdge(
                f"{record.id}:reverse",
                to_node,
                from_node,
                length_m,
                record.speed_kph,
                record.road_type,
                record.id,
                None,
                None,
                "backward",
            )
        )


def _interpolate_fraction(points, fraction: float):
    if not 0.0 <= fraction <= 1.0:
        raise ValueError("Connector fraction must be in [0, 1]")

    if fraction == 0.0:
        return points[0]
    if fraction == 1.0:
        return points[-1]

    total = sum(
        ((right.x - left.x) ** 2 + (right.y - left.y) ** 2) ** 0.5
        for left, right in zip(points, points[1:])
    )
    if total <= 0:
        return points[0]

    target = total * fraction
    traversed = 0.0
    for left, right in zip(points, points[1:]):
        segment = ((right.x - left.x) ** 2 + (right.y - left.y) ** 2) ** 0.5
        if traversed + segment >= target:
            ratio = (target - traversed) / segment if segment else 0.0
            from .geo import Point
            return Point(
                left.x + (right.x - left.x) * ratio,
                left.y + (right.y - left.y) * ratio,
            )
        traversed += segment

    return points[-1]
