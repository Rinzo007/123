from transit_planner.api import app


def test_api_has_core_routes():
    paths = {route.path for route in app.routes}
    assert "/health" in paths
    assert "/api/v1/network/validate" in paths
    assert "/api/v1/assignment" in paths
    assert "/api/v1/data/overture/stops" in paths
    assert "/api/v1/data/overture/network" in paths
    assert "/api/v1/data/overture/route" in paths
    assert "/api/v1/data/overture/roads" in paths
    assert "/api/v1/data/overture/connectors" in paths
    assert "/api/v1/scenario/compare" in paths
    assert "/api/v1/economics" in paths


def test_scenario_compare_endpoint_runs_two_networks():
    from transit_planner.api import compare_scenario_payload
    from transit_planner.network import Network, Route, Service, ServicePeriod, Stop, TransitMode, VehicleType
    from transit_planner.serialization import network_to_dict

    def make_network(headway: float):
        network = Network()
        network.add_stop(Stop("a", "A", __import__("transit_planner.geo", fromlist=["Point"]).Point(0, 0)))
        network.add_stop(Stop("b", "B", __import__("transit_planner.geo", fromlist=["Point"]).Point(1000, 0)))
        network.add_vehicle_type(VehicleType("bus", "Bus", TransitMode.BUS, 80))
        network.add_period(ServicePeriod("peak", 0, 60))
        network.add_route(Route("r1", "1", TransitMode.BUS, ("a", "b")))
        network.add_service(Service("svc", "r1", "bus", {"peak": headway}))
        return network

    def side(scenario_id: str, headway: float) -> dict:
        return {
            "id": scenario_id,
            "name": scenario_id,
            "network": network_to_dict(make_network(headway)),
            "demand": [{
                "origin_zone_id": "o",
                "destination_zone_id": "d",
                "trips_per_day": 100.0,
            }],
            "zones": [
                {"id": "o", "centroid_x": 0.0, "centroid_y": 0.0},
                {"id": "d", "centroid_x": 1000.0, "centroid_y": 0.0},
            ],
            "config": {"period_id": "peak", "max_access_distance_m": 0},
        }

    result = compare_scenario_payload({
        "base": side("base", 10.0),
        "alternative": side("alt", 5.0),
    })

    assert result["comparison"]["base_scenario_id"] == "base"
    assert result["comparison"]["alternative_scenario_id"] == "alt"
    assert any(item["metric"] == "transit_share" for item in result["comparison"]["metrics"])
    assert result["comparison"]["sections"]

    assert any(
        item["metric"] == "daily_operating_cost"
        for item in result["comparison"]["metrics"]
    )


def test_economics_endpoint_calculates_report():
    from transit_planner.api import calculate_economics_payload
    from transit_planner.network import Network, Route, Service, ServicePeriod, Stop, TransitMode, VehicleType
    from transit_planner.serialization import network_to_dict
    from transit_planner.geo import Point

    network = Network()
    network.add_stop(Stop("a", "A", Point(0, 0)))
    network.add_stop(Stop("b", "B", Point(1000, 0)))
    network.add_vehicle_type(VehicleType("bus", "Bus", TransitMode.BUS, 80, 2.0))
    network.add_period(ServicePeriod("peak", 0, 60))
    network.add_route(Route("r1", "1", TransitMode.BUS, ("a", "b")))
    network.add_service(Service("svc", "r1", "bus", {"peak": 10}))

    result = calculate_economics_payload({
        "id": "demo",
        "name": "Демо",
        "network": network_to_dict(network),
        "demand": [{
            "origin_zone_id": "a",
            "destination_zone_id": "b",
            "trips_per_day": 100.0,
        }],
        "zones": [
            {"id": "a", "centroid_x": 0.0, "centroid_y": 0.0},
            {"id": "b", "centroid_x": 1000.0, "centroid_y": 0.0},
        ],
        "config": {"period_id": "peak", "max_access_distance_m": 0},
        "economics_config": {
            "period_id": "peak",
            "fare_per_transit_trip": 2.0,
            "annual_days": 365,
        },
    })

    assert result["scenario_id"] == "demo"
    assert result["economics"]["daily_vehicle_km"] == 12.0
    assert result["economics"]["daily_fare_revenue"] > 0
    assert result["economics"]["annual_operating_cost"] == result["economics"]["daily_operating_cost"] * 365
