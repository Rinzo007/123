from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from .assignment import AssignmentConfig, assign_demand
from .city import DemandZone
from .demand import DemandMatrix, ODPairDemand
from .geojson import roads_to_geojson, stops_to_geojson
from .overture import (
    OvertureSource,
    OvertureTransitProvider,
    OvertureTransportationProvider,
)
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
    return OvertureSource(
        release=(release or DEFAULT_OVERTURE_RELEASE).strip()
    )


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
        bbox = _bbox(south, west, north, east)
        roads = OvertureTransportationProvider(
            source=_overture_source(release),
            bbox=bbox,
        ).load_roads()
    except (OSError, RuntimeError, TimeoutError) as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Overture недоступен: {exc}",
        ) from exc
    return roads_to_geojson(roads)


@app.get("/api/v1/data/overture/stops")
def overture_stops(
    south: float = Query(...),
    west: float = Query(...),
    north: float = Query(...),
    east: float = Query(...),
    release: str | None = Query(None),
) -> dict:
    try:
        bbox = _bbox(south, west, north, east)
        stops = OvertureTransitProvider(
            source=_overture_source(release),
            bbox=bbox,
        ).load_stops()
    except (OSError, RuntimeError, TimeoutError) as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Overture недоступен: {exc}",
        ) from exc
    return stops_to_geojson(stops)


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
            "transit_share": result.metrics.transit_share,
            "average_transit_time_min": result.metrics.average_transit_time_min,
            "average_transfers": result.metrics.average_transfers,
        },
        "iterations": result.iterations,
        "max_load_ratio": result.max_load_ratio,
        "unserved_transit_demand": result.unserved_transit_demand,
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
