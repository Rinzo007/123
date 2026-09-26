from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from .assignment import AssignmentConfig, assign_demand
from .calibration import ObservedRouteRidership, calibrate_route_ridership
from .city_demand import CityDemandConfig, build_city_demand, build_city_daily_demand
from .city import DemandZone
from .demand import DemandMatrix, ODPairDemand, expand_daily_demand
from .demand_streets import build_demand_streets, demand_streets_to_geojson
from .geojson import connectors_to_geojson, places_to_geojson, roads_to_geojson, stops_to_geojson, zones_to_geojson
from .projection import project_local_point_wgs84
from .overture import (
    OvertureConnectorProvider,
    OverturePlacesProvider,
    OvertureSource,
    OvertureTransitProvider,
    OvertureTransportationProvider,
)
from .overture_network import OvertureNetworkProvider
from .places import CityPlace
from .timetable import generate_service_timetable
from .zones import generate_zones_from_population_raster
from .serialization import network_from_dict

app = FastAPI(title="Transit Planner", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MAX_BBOX_AREA = 0.04
DEFAULT_OVERTURE_RELEASE = "2026-09-23.1"


def _bbox(
    south: float,
    west: float,
    north: float,
    east: float,
) -> tuple[float, float, float, float]:
    if not (-90 <= south < north <= 90 and -180 <= west < east <= 180):
        raise HTTPException(
            status_code=400,
            detail="Некорректная географическая область",
        )
    if (north - south) * (east - west) > MAX_BBOX_AREA:
        raise HTTPException(
            status_code=400,
            detail="Слишком большая область. Уменьшите масштаб карты.",
        )
    return south, west, north, east


def _overture_source(release: str | None) -> OvertureSource:
    return OvertureSource(release=(release or DEFAULT_OVERTURE_RELEASE).strip())


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "data_source": "overture",
        "overture_release": DEFAULT_OVERTURE_RELEASE,
    }


@app.post("/api/v1/network/validate")
def validate_network(payload: dict) -> dict:
    network = network_from_dict(payload)
    errors = network.validate()
    return {"valid": not errors, "errors": errors}


@app.get("/api/v1/data/overture/roads")
def overture_roads(
    south: float = Query(...),
    west: float = Query(...),
    north: float = Query(...),
    east: float = Query(...),
    release: str | None = Query(None),
) -> dict:
    try:
        roads = OvertureTransportationProvider(
            source=_overture_source(release),
            bbox=_bbox(south, west, north, east),
        ).load_roads()
    except (OSError, RuntimeError, TimeoutError) as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Overture недоступен: {exc}",
        ) from exc
    return roads_to_geojson(roads)


@app.get("/api/v1/data/overture/connectors")
def overture_connectors(
    south: float = Query(...),
    west: float = Query(...),
    north: float = Query(...),
    east: float = Query(...),
    release: str | None = Query(None),
) -> dict:
    try:
        connectors = OvertureConnectorProvider(
            source=_overture_source(release),
            bbox=_bbox(south, west, north, east),
        ).load_connectors()
    except (OSError, RuntimeError, TimeoutError) as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Overture недоступен: {exc}",
        ) from exc
    return connectors_to_geojson(connectors)


@app.get("/api/v1/data/overture/network")
def overture_network(
    south: float = Query(...),
    west: float = Query(...),
    north: float = Query(...),
    east: float = Query(...),
    release: str | None = Query(None),
) -> dict:
    source = _overture_source(release)
    bounds = _bbox(south, west, north, east)

    def roads_task():
        return OvertureTransportationProvider(source=source, bbox=bounds).load_roads()

    def connectors_task():
        return OvertureConnectorProvider(source=source, bbox=bounds).load_connectors()

    def stops_task():
        return OvertureTransitProvider(source=source, bbox=bounds).load_stops()

    def places_task():
        return OverturePlacesProvider(source=source, bbox=bounds).load_places()

    try:
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="overture") as pool:
            roads_future = pool.submit(roads_task)
            connectors_future = pool.submit(connectors_task)
            stops_future = pool.submit(stops_task)
            places_future = pool.submit(places_task)
            roads = roads_future.result()
            connectors = connectors_future.result()
            stops = stops_future.result()
            places = places_future.result()
    except (OSError, RuntimeError, TimeoutError) as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Overture недоступен: {exc}",
        ) from exc

    return {
        "roads": roads_to_geojson(roads),
        "connectors": connectors_to_geojson(connectors),
        "stops": stops_to_geojson(stops),
        "places": places_to_geojson(places),
        "release": source.release,
        "counts": {
            "roads": len(roads),
            "connectors": len(connectors),
            "stops": len(stops),
            "places": len(places),
        },
    }


@app.post("/api/v1/data/overture/route")
def overture_route(payload: dict) -> dict:
    try:
        bounds = _bbox(
            float(payload["south"]),
            float(payload["west"]),
            float(payload["north"]),
            float(payload["east"]),
        )
        raw_points = payload.get("points", [])
        if not isinstance(raw_points, list) or len(raw_points) < 2:
            raise HTTPException(status_code=400, detail="Для маршрута нужны минимум две точки")
        if len(raw_points) > 100:
            raise HTTPException(status_code=400, detail="Слишком много точек маршрута")

        from .geo import Point

        points = tuple(Point(float(item["lon"]), float(item["lat"])) for item in raw_points)
        overture_network = OvertureNetworkProvider(
            source=_overture_source(payload.get("release")),
            bbox=bounds,
            snap_max_distance_m=float(payload.get("snap_distance_m", 150.0)),
        ).load(include_connectors=False, include_stops=False, include_places=False)
        route = overture_network.route_points(points)
    except HTTPException:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Некорректный маршрут: {exc}") from exc
    except (OSError, RuntimeError, TimeoutError) as exc:
        raise HTTPException(status_code=502, detail=f"Overture недоступен: {exc}") from exc

    geometry = tuple(
        project_local_point_wgs84(
            point,
            origin_lon=overture_network.origin_lon,
            origin_lat=overture_network.origin_lat,
        )
        for point in route.geometry
    )
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": [[point.x, point.y] for point in geometry]},
        "properties": {
            "edge_ids": list(route.edge_ids),
            "length_m": route.length_m,
            "travel_time_min": route.travel_time_min,
            "snap_distances_m": list(route.snap_distances_m),
        },
    }

@app.get("/api/v1/data/overture/places")
def overture_places(
    south: float = Query(...),
    west: float = Query(...),
    north: float = Query(...),
    east: float = Query(...),
    release: str | None = Query(None),
) -> dict:
    try:
        places = OverturePlacesProvider(
            source=_overture_source(release),
            bbox=_bbox(south, west, north, east),
        ).load_places()
    except (OSError, RuntimeError, TimeoutError) as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Overture недоступен: {exc}",
        ) from exc
    return places_to_geojson(places)

@app.get("/api/v1/data/overture/stops")
def overture_stops(
    south: float = Query(...),
    west: float = Query(...),
    north: float = Query(...),
    east: float = Query(...),
    release: str | None = Query(None),
) -> dict:
    try:
        stops = OvertureTransitProvider(
            source=_overture_source(release),
            bbox=_bbox(south, west, north, east),
        ).load_stops()
    except (OSError, RuntimeError, TimeoutError) as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Overture недоступен: {exc}",
        ) from exc
    return stops_to_geojson(stops)


@app.post("/api/v1/calibration/route-ridership")
def route_ridership_calibration(payload: dict) -> dict:
    observed = tuple(
        ObservedRouteRidership(
            route_id=str(item["route_id"]),
            observed_boardings_per_day=float(item["observed_boardings_per_day"]),
        )
        for item in payload.get("observed", [])
    )
    simulated = {str(key): float(value) for key, value in payload.get("simulated", {}).items()}
    report = calibrate_route_ridership(observed, simulated)
    return {
        "mae": report.mae,
        "rmse": report.rmse,
        "mape": report.mape,
        "routes": [
            {
                "route_id": item.route_id,
                "observed": item.observed,
                "simulated": item.simulated,
                "absolute_error": item.absolute_error,
                "relative_error": item.relative_error,
            }
            for item in report.routes
        ],
    }

@app.post("/api/v1/timetable")
def create_timetable(payload: dict) -> dict:
    service_id = str(payload.get("service_id", "service"))
    periods = payload.get("periods", {})
    headways = payload.get("headway_by_period", {})
    timetable = generate_service_timetable(
        service_id,
        {str(key): (int(value["start_minute"]), int(value["end_minute"])) for key, value in periods.items()},
        {str(key): float(value) for key, value in headways.items()},
        offset_minute=int(payload.get("offset_minute", 0)),
    )
    return {
        "service_id": timetable.service_id,
        "periods": [
            {
                "period_id": period.period_id,
                "departures_minute": list(period.departures_minute),
            }
            for period in timetable.periods
        ],
    }

@app.get("/api/v1/demand/population-zones")
def population_zones(
    south: float = Query(...),
    west: float = Query(...),
    north: float = Query(...),
    east: float = Query(...),
) -> dict:
    raster_path = os.getenv("TRANSIT_PLANNER_POPULATION_RASTER")
    if not raster_path:
        raise HTTPException(status_code=503, detail="TRANSIT_PLANNER_POPULATION_RASTER не настроен")
    try:
        zones = generate_zones_from_population_raster(
            raster_path,
            bbox=_bbox(south, west, north, east),
            origin_lon=(west + east) / 2.0,
            origin_lat=(south + north) / 2.0,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"Не удалось прочитать population raster: {exc}") from exc
    return zones_to_geojson(
        zones,
        origin_lon=(west + east) / 2.0,
        origin_lat=(south + north) / 2.0,
    )

@app.post("/api/v1/demand/city")
def city_demand(payload: dict) -> dict:
    from .geo import Point

    zones = tuple(
        DemandZone(
            id=str(item["id"]),
            centroid_x=float(item["centroid_x"]),
            centroid_y=float(item["centroid_y"]),
            population=float(item.get("population", 0.0)),
            jobs=float(item.get("jobs", 0.0)),
        )
        for item in payload.get("zones", [])
    )
    places = tuple(
        CityPlace(
            id=str(item["id"]),
            name=str(item.get("name", item["id"])),
            location=Point(float(item["lon"]), float(item["lat"])),
            basic_category=item.get("basic_category"),
            taxonomy_primary=item.get("taxonomy_primary"),
            importance=float(item.get("importance", 1.0)),
        )
        for item in payload.get("places", [])
    )
    config = CityDemandConfig(
        trip_rate=float(payload.get("trip_rate", 0.12)),
        decay=float(payload.get("decay", 0.08)),
        reference_speed_kph=float(payload.get("reference_speed_kph", 30.0)),
    )
    model = str(payload.get("model", "default")).strip().lower()
    if model == "reference":
        demand = build_city_daily_demand(
            zones,
            places,
            origin_lon=payload.get("origin_lon"),
            origin_lat=payload.get("origin_lat"),
        )
    else:
        demand = build_city_demand(
            zones,
            places,
            origin_lon=payload.get("origin_lon"),
            origin_lat=payload.get("origin_lat"),
            config=config,
        )
    return {
        "model": model,
        "total_trips_per_day": demand.total_trips_per_day,
        "pairs": [
            {
                "origin_zone_id": pair.origin_zone_id,
                "destination_zone_id": pair.destination_zone_id,
                "trips_per_day": pair.trips_per_day,
                "purpose": pair.purpose,
            }
            for pair in demand.pairs
        ],
    }

@app.post("/api/v1/demand/streets")
def demand_streets(payload: dict) -> dict:
    pairs = tuple(
        ODPairDemand(
            str(item["origin_zone_id"]),
            str(item["destination_zone_id"]),
            float(item["trips_per_day"]),
            str(item.get("purpose", "all")),
        )
        for item in payload.get("demand", [])
    )
    zones = {
        str(item["id"]): DemandZone(
            id=str(item["id"]),
            centroid_x=float(item["centroid_x"]),
            centroid_y=float(item["centroid_y"]),
        )
        for item in payload.get("zones", [])
    }
    streets = build_demand_streets(
        pairs,
        zones,
        min_trips=float(payload.get("min_trips", 0.0)),
    )
    return demand_streets_to_geojson(
        streets,
        zones,
        origin_lon=float(payload.get("origin_lon", 0.0)),
        origin_lat=float(payload.get("origin_lat", 0.0)),
    )

@app.post("/api/v1/demand/temporal")
def temporal_demand(payload: dict) -> dict:
    pairs = tuple(
        ODPairDemand(
            str(item["origin_zone_id"]),
            str(item["destination_zone_id"]),
            float(item["trips_per_day"]),
            str(item.get("purpose", "all")),
        )
        for item in payload.get("demand", [])
    )
    temporal = expand_daily_demand(DemandMatrix(pairs))
    return {
        "total_trips": temporal.total_trips,
        "period_totals": temporal.period_totals(),
        "pairs": [
            {
                "origin_zone_id": pair.origin_zone_id,
                "destination_zone_id": pair.destination_zone_id,
                "period_id": pair.period_id,
                "trips": pair.trips,
                "purpose": pair.purpose,
            }
            for pair in temporal.pairs
        ],
    }

@app.post("/api/v1/assignment/city")
def city_assignment(payload: dict) -> dict:
    raster_path = os.getenv("TRANSIT_PLANNER_POPULATION_RASTER")
    if not raster_path:
        raise HTTPException(status_code=503, detail="TRANSIT_PLANNER_POPULATION_RASTER не настроен")

    try:
        network = network_from_dict(payload["network"])
        south = float(payload["south"])
        west = float(payload["west"])
        north = float(payload["north"])
        east = float(payload["east"])
        bounds = _bbox(south, west, north, east)
        origin_lon = float(payload.get("origin_lon", (west + east) / 2.0))
        origin_lat = float(payload.get("origin_lat", (south + north) / 2.0))
        zones = generate_zones_from_population_raster(
            raster_path,
            bbox=bounds,
            origin_lon=origin_lon,
            origin_lat=origin_lat,
        )
        places = OverturePlacesProvider(
            source=_overture_source(payload.get("release")),
            bbox=bounds,
        ).load_places()
        demand = build_city_demand(
            zones,
            places,
            origin_lon=origin_lon,
            origin_lat=origin_lat,
            config=CityDemandConfig(
                trip_rate=float(payload.get("trip_rate", 0.12)),
                decay=float(payload.get("decay", 0.08)),
                reference_speed_kph=float(payload.get("reference_speed_kph", 30.0)),
            ),
        )
        result = assign_demand(
            network,
            demand,
            zones={zone.id: zone for zone in zones},
            config=AssignmentConfig(**payload["config"]),
        )
    except HTTPException:
        raise
    except (KeyError, TypeError, ValueError, OSError, RuntimeError, TimeoutError) as exc:
        raise HTTPException(status_code=502, detail=f"Citywide demand calculation failed: {exc}") from exc

    return {
        "data": {
            "zones": len(zones),
            "places": len(places),
            "od_pairs": len(demand.pairs),
            "total_demand_trips": demand.total_trips_per_day,
        },
        "assignment": {
            "metrics": {
                "total_trips": result.metrics.total_trips,
                "transit_trips": result.metrics.transit_trips,
                "car_trips": result.metrics.car_trips,
                "walk_trips": result.metrics.walk_trips,
                "bike_trips": result.metrics.bike_trips,
                "transit_share": result.metrics.transit_share,
                "average_transit_time_min": result.metrics.average_transit_time_min,
                "average_transfers": result.metrics.average_transfers,
            },
            "max_load_ratio": result.max_load_ratio,
            "unserved_transit_demand": result.unserved_transit_demand,
            "loss_reasons": [{"reason": item.reason, "trips": item.trips} for item in result.loss_reasons],
            "route_flows": [{"route_id": item.route_id, "boardings": item.boardings, "passenger_section_traversals": item.passenger_section_traversals} for item in result.route_flows],
            "section_loads": [{"route_id": item.route_id, "from_stop_id": item.from_stop_id, "to_stop_id": item.to_stop_id, "passengers": item.passengers, "capacity": item.capacity, "load_ratio": item.load_ratio} for item in result.section_loads],
            "stop_flows": [{"stop_id": item.stop_id, "boardings": item.boardings, "alightings": item.alightings, "transfers": item.transfers} for item in result.stop_flows],
        },
    }

@app.post("/api/v1/assignment")
def calculate_assignment(payload: dict) -> dict:
    network = network_from_dict(payload["network"])
    pairs = tuple(
        ODPairDemand(
            origin_zone_id=str(item["origin_zone_id"]),
            destination_zone_id=str(item["destination_zone_id"]),
            trips_per_day=float(item["trips_per_day"]),
            purpose=str(item.get("purpose", "all")),
        )
        for item in payload.get("demand", [])
    )
    zones = {
        str(item["id"]): DemandZone(
            id=str(item["id"]),
            centroid_x=float(item["centroid_x"]),
            centroid_y=float(item["centroid_y"]),
            population=float(item.get("population", 0.0)),
            jobs=float(item.get("jobs", 0.0)),
        )
        for item in payload.get("zones", [])
    }
    config = AssignmentConfig(**payload["config"])
    result = assign_demand(
        network,
        DemandMatrix(pairs),
        zones=zones,
        config=config,
    )
    return {
        "metrics": {
            "total_trips": result.metrics.total_trips,
            "transit_trips": result.metrics.transit_trips,
            "car_trips": result.metrics.car_trips,
            "walk_trips": result.metrics.walk_trips,
            "bike_trips": result.metrics.bike_trips,
            "transit_share": result.metrics.transit_share,
            "average_transit_time_min": result.metrics.average_transit_time_min,
            "average_transfers": result.metrics.average_transfers,
        },
        "iterations": result.iterations,
        "max_load_ratio": result.max_load_ratio,
        "unserved_transit_demand": result.unserved_transit_demand,
        "loss_reasons": [{"reason": item.reason, "trips": item.trips} for item in result.loss_reasons],
        "route_flows": [
            {
                "route_id": item.route_id,
                "boardings": item.boardings,
                "passenger_section_traversals": item.passenger_section_traversals,
            }
            for item in result.route_flows
        ],
        "section_loads": [
            {
                "route_id": item.route_id,
                "from_stop_id": item.from_stop_id,
                "to_stop_id": item.to_stop_id,
                "passengers": item.passengers,
                "capacity": item.capacity,
                "load_ratio": item.load_ratio,
            }
            for item in result.section_loads
        ],
        "stop_flows": [
            {
                "stop_id": item.stop_id,
                "boardings": item.boardings,
                "alightings": item.alightings,
                "transfers": item.transfers,
            }
            for item in result.stop_flows
        ],
    }
