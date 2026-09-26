from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from statistics import fmean

from .overture import (
    OvertureConnectorProvider,
    OvertureSource,
    OvertureTransitProvider,
    OvertureTransportationProvider,
)
from .projection import project_roads_wgs84, project_stops_wgs84
from .road import RoadGraph
from .road_builder import RoadGraphBuildResult, build_topological_road_graph
from .snap import StopSnap, snap_stops_to_road_graph
from .data import ConnectorRecord, RoadRecord
from .geo import Point
from .network import Stop


@dataclass(frozen=True, slots=True)
class RoadRouteResult:
    edge_ids: tuple[str, ...]
    geometry: tuple[Point, ...]
    length_m: float
    travel_time_min: float
    snap_distances_m: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class OvertureNetwork:
    roads: tuple[RoadRecord, ...]
    connectors: tuple[ConnectorRecord, ...]
    stops: tuple[Stop, ...]
    roads_metric: tuple[RoadRecord, ...]
    stops_metric: tuple[Stop, ...]
    graph: RoadGraph
    graph_build: RoadGraphBuildResult
    stop_snaps: tuple[StopSnap, ...]
    origin_lon: float
    origin_lat: float

    def route_points(
        self,
        points_wgs84: tuple[Point, ...],
        *,
        snap_max_distance_m: float | None = 150.0,
    ) -> RoadRouteResult:
        if len(points_wgs84) < 2:
            raise ValueError("A road route needs at least two points")

        from .projection import project_wgs84_point
        metric_points = tuple(
            project_wgs84_point(
                point,
                origin_lon=self.origin_lon,
                origin_lat=self.origin_lat,
            )
            for point in points_wgs84
        )
        route_stops = tuple(
            Stop(
                f"route-point-{index}",
                f"Route point {index + 1}",
                point,
            )
            for index, point in enumerate(metric_points)
        )
        snaps = snap_stops_to_road_graph(
            route_stops,
            self.graph,
            max_distance=snap_max_distance_m,
        )
        edge_ids: list[str] = []
        for index in range(len(route_stops) - 1):
            start_snap = snaps[index]
            end_snap = snaps[index + 1]
            if start_snap.road_node_id is None or end_snap.road_node_id is None:
                raise ValueError("A route point is too far from the road graph")
            _, path = self.graph.shortest_path(
                start_snap.road_node_id,
                end_snap.road_node_id,
            )
            if not path:
                raise ValueError("No road path exists between consecutive route points")
            edge_ids.extend(path)

        edge_path = tuple(edge_ids)
        return RoadRouteResult(
            edge_ids=edge_path,
            geometry=self.graph.path_geometry(edge_path),
            length_m=self.graph.path_length_m(edge_path),
            travel_time_min=self.graph.path_travel_time_minutes(edge_path),
            snap_distances_m=tuple(snap.distance for snap in snaps),
        )



def build_overture_network(
    roads: tuple[RoadRecord, ...],
    connectors: tuple[ConnectorRecord, ...],
    stops: tuple[Stop, ...],
    *,
    origin_lon: float,
    origin_lat: float,
    snap_max_distance_m: float | None = 150.0,
) -> OvertureNetwork:
    roads_metric = project_roads_wgs84(
        roads,
        origin_lon=origin_lon,
        origin_lat=origin_lat,
    )
    stops_metric = project_stops_wgs84(
        stops,
        origin_lon=origin_lon,
        origin_lat=origin_lat,
    )
    graph_build = build_topological_road_graph(roads_metric)
    stop_snaps = snap_stops_to_road_graph(
        stops_metric,
        graph_build.graph,
        max_distance=snap_max_distance_m,
    )
    return OvertureNetwork(
        roads=roads,
        connectors=connectors,
        stops=stops,
        roads_metric=roads_metric,
        stops_metric=stops_metric,
        graph=graph_build.graph,
        graph_build=graph_build,
        stop_snaps=stop_snaps,
        origin_lon=origin_lon,
        origin_lat=origin_lat,
    )


class OvertureNetworkProvider:
    """Load and assemble all active Overture city layers into one network object."""

    def __init__(
        self,
        *,
        source: OvertureSource = OvertureSource(),
        bbox: tuple[float, float, float, float] | None = None,
        snap_max_distance_m: float | None = 150.0,
    ) -> None:
        self.source = source
        self.bbox = bbox
        self.snap_max_distance_m = snap_max_distance_m

    def load(
        self,
        *,
        include_connectors: bool = True,
        include_stops: bool = True,
    ) -> OvertureNetwork:
        transportation = OvertureTransportationProvider(
            source=self.source,
            bbox=self.bbox,
        )
        connector_provider = OvertureConnectorProvider(
            source=self.source,
            bbox=self.bbox,
        )
        transit = OvertureTransitProvider(
            source=self.source,
            bbox=self.bbox,
        )

        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="overture") as pool:
            roads_future = pool.submit(transportation.load_roads)
            connectors_future = (
                pool.submit(connector_provider.load_connectors)
                if include_connectors
                else None
            )
            stops_future = (
                pool.submit(transit.load_stops)
                if include_stops
                else None
            )
            roads = roads_future.result()
            connectors = () if connectors_future is None else connectors_future.result()
            stops = () if stops_future is None else stops_future.result()

        origin_lon, origin_lat = self._origin(stops)
        return build_overture_network(
            roads,
            connectors,
            stops,
            origin_lon=origin_lon,
            origin_lat=origin_lat,
            snap_max_distance_m=self.snap_max_distance_m,
        )

    def _origin(self, stops: tuple[Stop, ...]) -> tuple[float, float]:
        if stops:
            return (
                fmean(stop.location.x for stop in stops),
                fmean(stop.location.y for stop in stops),
            )
        if self.bbox is not None:
            south, west, north, east = self.bbox
            return ((west + east) / 2.0, (south + north) / 2.0)
        return (0.0, 0.0)
