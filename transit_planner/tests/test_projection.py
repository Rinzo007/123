from transit_planner.geo import Point
from transit_planner.projection import project_wgs84_point


def test_wgs84_projection_is_metric_at_city_scale():
    origin = Point(39.0, 51.0)
    east = Point(39.001, 51.0)
    north = Point(39.0, 51.001)

    p_east = project_wgs84_point(east, origin_lon=39.0, origin_lat=51.0)
    p_north = project_wgs84_point(north, origin_lon=39.0, origin_lat=51.0)

    assert 60 < p_east.x < 80
    assert abs(p_east.y) < 1e-9
    assert 100 < p_north.y < 120
    assert abs(p_north.x) < 1e-9
