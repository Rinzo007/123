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

    def load(self) -> OvertureNetwork:
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
            connectors_future = pool.submit(connector_provider.load_connectors)
            stops_future = pool.submit(transit.load_stops)
            roads = roads_future.result()
            connectors = connectors_future.result()
            stops = stops_future.result()

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
