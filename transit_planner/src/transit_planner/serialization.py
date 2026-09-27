from __future__ import annotations

import json
from dataclasses import asdict

from .geo import LineString, Point
from .network import Network, Route, Service, ServicePeriod, Stop, TrackRow, TransitMode, VehicleType
from .stations import Platform, PlatformLayout, Station, StationGroup
from .rolling_stock import RollingStockType
from .fares import FareGroup, FareSystem, FareZone, TransferPolicy


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
                "row_by_segment": [row.value for row in route.row_by_segment],
                "open_stop_ids": list(route.open_stop_ids),
            }
            for route in network.routes.values()
        ],
        "vehicle_types": [
            asdict(vehicle) | {"mode": vehicle.mode.value}
            for vehicle in network.vehicle_types.values()
        ],
        "periods": [asdict(period) for period in network.periods.values()],
        "services": [asdict(service) for service in network.services.values()],
        "track_sections": [
            asdict(section) | {"track_type": section.track_type.value}
            for section in network.track_sections.values()
        ],
        "stations": [asdict(station) for station in network.stations.values()],
        "platforms": [
            asdict(platform) | {"layout": platform.layout.value}
            for platform in network.platforms.values()
        ],
        "station_groups": [asdict(group) for group in network.station_groups.values()],
        "rolling_stock": [asdict(stock) for stock in network.rolling_stock.values()],
        "fare_groups": [
            asdict(group) | {
                "fare_system": group.fare_system.value,
                "transfer_policy": group.transfer_policy.value,
                "zones": [asdict(zone) for zone in group.zones],
            }
            for group in network.fare_groups.values()
        ],
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
                speed_limit_kph=(
                    None
                    if raw.get("speed_limit_kph") is None
                    else float(raw["speed_limit_kph"])
                ),
            )
        )

    for raw in data.get("stations", []):
        network.add_station(
            Station(
                raw["id"], raw["name"], raw["stop_id"],
                tuple(raw.get("platform_ids", ())),
                raw.get("group_id"),
                bool(raw.get("interchange", False)),
                raw.get("platform_length_m"),
            )
        )
    for raw in data.get("platforms", []):
        network.add_platform(
            Platform(
                raw["id"], raw["station_id"], float(raw["length_m"]),
                tuple(raw.get("track_ids", ())),
                PlatformLayout(raw.get("layout", "side")),
                int(raw.get("number", 1)),
            )
        )
    for raw in data.get("station_groups", []):
        network.add_station_group(
            StationGroup(
                raw["id"], raw["name"], tuple(raw["station_ids"]),
                float(raw.get("transfer_walk_min", 0.0)),
            )
        )
    for raw in data.get("rolling_stock", []):
        network.add_rolling_stock(RollingStockType(**raw))
    for raw in data.get("fare_groups", []):
        network.add_fare_group(
            FareGroup(
                id=raw["id"], name=raw["name"],
                fare_system=FareSystem(raw.get("fare_system", "flat")),
                flat_fare=float(raw.get("flat_fare", 0.0)),
                route_fares=dict(raw.get("route_fares", {})),
                transfer_policy=TransferPolicy(raw.get("transfer_policy", "none")),
                transfer_window_min=float(raw.get("transfer_window_min", 0.0)),
                boarding_charge=float(raw.get("boarding_charge", 0.0)),
                per_km_rate=float(raw.get("per_km_rate", 0.0)),
                fare_cap=raw.get("fare_cap"),
                zones=tuple(FareZone(**zone) for zone in raw.get("zones", ())),
                zone_base_fare=float(raw.get("zone_base_fare", 0.0)),
                zone_per_zone_fare=float(raw.get("zone_per_zone_fare", 0.0)),
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
                tuple(TrackRow(raw_row) for raw_row in raw.get("row_by_segment", ())),
                tuple(raw.get("open_stop_ids", ())),
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
                dict(raw.get("phase_by_period", {})),
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
