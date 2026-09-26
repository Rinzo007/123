from __future__ import annotations

from dataclasses import dataclass

from .network import Stop
from .road import RoadGraph
from .spatial import GridPointIndex, IndexedPoint


@dataclass(frozen=True, slots=True)
class StopSnap:
    stop_id: str
    road_node_id: int | None
    distance: float


def snap_stops_to_road_graph(
    stops: tuple[Stop, ...],
    graph: RoadGraph,
    *,
    cell_size: float = 250.0,
    max_distance: float | None = None,
) -> tuple[StopSnap, ...]:
    index = GridPointIndex(cell_size=cell_size)
    for node in graph.nodes.values():
        index.insert(IndexedPoint(node.id, node.x, node.y))

    result: list[StopSnap] = []
    for stop in stops:
        nearest = index.nearest(
            stop.location.x,
            stop.location.y,
            max_radius=max_distance,
        )
        distance = (
            float("inf")
            if nearest is None
            else ((nearest.x - stop.location.x) ** 2 + (nearest.y - stop.location.y) ** 2) ** 0.5
        )
        result.append(
            StopSnap(
                stop_id=stop.id,
                road_node_id=None if nearest is None else nearest.id,
                distance=distance,
            )
        )
    return tuple(result)
