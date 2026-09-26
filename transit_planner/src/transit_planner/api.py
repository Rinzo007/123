from __future__ import annotations

from fastapi import FastAPI

from .assignment import AssignmentConfig, assign_demand
from .city import DemandZone
from .demand import DemandMatrix, ODPairDemand
from .serialization import network_from_dict

app = FastAPI(title="Transit Planner", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/v1/network/validate")
def validate_network(payload: dict) -> dict:
    network = network_from_dict(payload)
    errors = network.validate()
    return {"valid": not errors, "errors": errors}


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
    }
