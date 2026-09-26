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
