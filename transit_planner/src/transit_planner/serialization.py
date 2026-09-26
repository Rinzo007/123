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
                "track_section_ids": list(route.track_section_ids),
                "both_ways": route.both_ways,
                "closed": route.closed,
            }
            for route in network.routes.values()
        ],
        "vehicle_types": [
            asdict(vehicle) | {"mode": vehicle.mode.value}
            for vehicle in network.vehicle_types.values()
        ],
        "periods": [asdict(period) for period in network.periods.values()],
        "services": [asdict(service) for service in network.services.values()],
        "track_sections": [asdict(section) for section in network.track_sections.values()],
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
    from .infrastructure import TrackSection, TrackType

    for raw in data.get("track_sections", []):
        network.add_track_section(
            TrackSection(
                id=raw["id"],
                length_km=float(raw["length_km"]),
                track_type=TrackType(raw.get("track_type", "surface")),
                capacity_departures_per_hour=float(raw.get("capacity_departures_per_hour", 30.0)),
                shared_group=raw.get("shared_group"),
                station_ids=tuple(raw.get("station_ids", ())),
            )
        )

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
                tuple(raw.get("track_section_ids", ())),
                bool(raw.get("both_ways", True)),
                bool(raw.get("closed", False)),
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
