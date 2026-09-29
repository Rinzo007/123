from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from statistics import fmean
from typing import Callable

from .overture import (
    OvertureConnectorProvider,
    OvertureSource,
    OvertureTransitProvider,
    OvertureTransportationProvider,
    OverturePlacesProvider,
)
from .projection import project_roads_wgs84, project_stops_wgs84, project_wgs84_point
from .road import RoadGraph
from .road_builder import RoadGraphBuildResult, build_topological_road_graph
from .snap import StopSnap, snap_stops_to_road_graph
from .data import ConnectorRecord, RoadRecord
from .geo import Point
from .places import CityPlace
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
    places: tuple[CityPlace, ...]
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
        components = self.graph.weakly_connected_components()
        for index in range(len(route_stops) - 1):
            start_snap = snaps[index]
            end_snap = snaps[index + 1]
            if start_snap.road_node_id is None or end_snap.road_node_id is None:
                raise ValueError("A route point is too far from the road graph")
            if components[start_snap.road_node_id] != components[end_snap.road_node_id]:
                raise ValueError(
                    "Route points lie in disconnected road graph components; "
                    "the street network is missing a connecting segment between them"
                )
            _, path = self.graph.shortest_path(
                start_snap.road_node_id,
                end_snap.road_node_id,
            )
            if not path:
                raise ValueError(
                    "No directed road path exists between consecutive route points "
                    "within their connected component (check one-way restrictions)"
                )
            edge_ids.extend(path)

        edge_path = tuple(edge_ids)
        return RoadRouteResult(
            edge_ids=edge_path,
            geometry=self.graph.path_geometry(edge_path),
            length_m=self.graph.path_length_m(edge_path),
            travel_time_min=self.graph.path_travel_time_minutes(edge_path),
            snap_distances_m=tuple(snap.distance for snap in snaps),
        )



def _road_midpoint(record: RoadRecord) -> Point:
    """Середина геометрии дороги в WGS84: по ней режется область исследования."""
    points = record.geometry.points
    if not points:
        return Point(0.0, 0.0)
    return Point(
        fmean(point.x for point in points),
        fmean(point.y for point in points),
    )


def build_overture_network(
    roads: tuple[RoadRecord, ...],
    connectors: tuple[ConnectorRecord, ...],
    stops: tuple[Stop, ...],
    places: tuple[CityPlace, ...] = (),
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
    graph_build = build_topological_road_graph(
        roads_metric,
        # Дороги проецированы в локальные метры, поэтому позиции коннекторов
        # обязаны быть в тех же единицах, иначе узел встал бы на другой конец
        # города. Система координат дорог и коннекторов обязана совпадать.
        connector_locations={
            record.id: project_wgs84_point(
                record.location,
                origin_lon=origin_lon,
                origin_lat=origin_lat,
            )
            for record in connectors
        },
    )
    stop_snaps = snap_stops_to_road_graph(
        stops_metric,
        graph_build.graph,
        max_distance=snap_max_distance_m,
    )
    return OvertureNetwork(
        roads=roads,
        connectors=connectors,
        stops=stops,
        places=places,
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
        include_places: bool = True,
        area_filter: Callable[[Point], bool] | None = None,
    ) -> OvertureNetwork:
        """Читает сеть и, если задан `area_filter`, отсекает её по области.

        Фильтр применяется до сборки графа, а не после: дорога вне области не
        должна ни попасть в граф, ни стянуть за собой соседей через коннектор.
        Дороги режутся по середине, точки - по своему положению.
        """
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
        places_provider = OverturePlacesProvider(
            source=self.source,
            bbox=self.bbox,
        )

        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="overture") as pool:
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
            places_future = (
                pool.submit(places_provider.load_places)
                if include_places
                else None
            )
            roads = roads_future.result()
            connectors = () if connectors_future is None else connectors_future.result()
            stops = () if stops_future is None else stops_future.result()
            places = () if places_future is None else places_future.result()

        if area_filter is not None:
            roads = tuple(record for record in roads if area_filter(_road_midpoint(record)))
            # Коннекторы оставляем по ссылкам уцелевших дорог, а не по своему
            # положению: коннектор на шоссе у самой границы лежит вне полигона,
            # но дорога внутри границы на него опирается. Отбросив его, мы бы
            # заставили сборщик графа интерполировать узел и получить конфликт
            # координат, который он честно отказывается разрешать молча.
            referenced = {
                ref.connector_id for record in roads for ref in record.connectors
            }
            connectors = tuple(
                record for record in connectors if record.id in referenced
            )
            stops = tuple(stop for stop in stops if area_filter(stop.location))
            places = tuple(place for place in places if area_filter(place.location))

        origin_lon, origin_lat = self._origin(stops)
        return build_overture_network(
            roads,
            connectors,
            stops,
            places,
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
