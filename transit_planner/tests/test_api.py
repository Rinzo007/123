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
