from __future__ import annotations

import json
from dataclasses import asdict

from .geo import LineString, Point
from .network import Network, Route, Service, ServicePeriod, Stop, TransitMode, VehicleType


def network_to_dict(network: Network) -> dict:
    return {
        "stops": [
            {
                "id": stop.id,
                "name": stop.name,
                "location": {"x": stop.location.x, "y": stop.location.y},
                "is_station": stop.is_station,
            }
            for stop in network.stops.values()
        ],
        "routes": [
            {
                "id": route.id,
                "name": route.name,
                "mode": route.mode.value,
                "stop_ids": list(route.stop_ids),
                "geometry": None
                if route.geometry is None
                else {
                    "points": [{"x": p.x, "y": p.y} for p in route.geometry.points]
                },
            }
            for route in network.routes.values()
        ],
        "vehicle_types": [
            asdict(vehicle) | {"mode": vehicle.mode.value}
            for vehicle in network.vehicle_types.values()
        ],
        "periods": [asdict(period) for period in network.periods.values()],
        "services": [asdict(service) for service in network.services.values()],
    }


def network_from_dict(data: dict) -> Network:
    network = Network()
    for raw in data.get("stops", []):
        network.add_stop(
            Stop(
                raw["id"],
                raw["name"],
                Point(**raw["location"]),
                raw.get("is_station", False),
            )
        )
    for raw in data.get("vehicle_types", []):
        network.add_vehicle_type(
            VehicleType(
                raw["id"],
                raw["name"],
                TransitMode(raw["mode"]),
                raw["capacity"],
                raw.get("operating_cost_per_km", 0.0),
            )
        )
    for raw in data.get("periods", []):
        network.add_period(ServicePeriod(**raw))
    for raw in data.get("routes", []):
        geometry = raw.get("geometry")
        line = None
        if geometry:
            line = LineString(tuple(Point(**p) for p in geometry["points"]))
        network.add_route(
            Route(
                raw["id"],
                raw["name"],
                TransitMode(raw["mode"]),
                tuple(raw["stop_ids"]),
                line,
            )
        )
    for raw in data.get("services", []):
        network.add_service(
            Service(
                raw["id"],
                raw["route_id"],
                raw["vehicle_type_id"],
                dict(raw["headway_by_period"]),
                dict(raw.get("departure_offset_by_period", {})),
            )
        )
    return network


def dumps_network(network: Network) -> str:
    return json.dumps(
        network_to_dict(network),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )


def loads_network(payload: str) -> Network:
    return network_from_dict(json.loads(payload))
