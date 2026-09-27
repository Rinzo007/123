from transit_planner.geo import LineString, Point
from transit_planner.urban import (
    BuildingFootprint,
    UrbanContext,
    WaterFeature,
    parse_polygon_geometry,
)


def _square(lon: float, lat: float, half: float = 0.0002):
    return (
        Point(lon - half, lat - half),
        Point(lon + half, lat - half),
        Point(lon + half, lat + half),
        Point(lon - half, lat + half),
        Point(lon - half, lat - half),
    )


def test_parse_polygon_geometry_supports_polygon_and_multipolygon():
    polygon = {
        "type": "Polygon",
        "coordinates": [[
            [39.0, 51.0],
            [39.001, 51.0],
            [39.001, 51.001],
            [39.0, 51.0],
        ]],
    }
    multipolygon = {
        "type": "MultiPolygon",
        "coordinates": [[[
            [39.0, 51.0],
            [39.001, 51.0],
            [39.001, 51.001],
            [39.0, 51.0],
        ]]],
    }
    assert len(parse_polygon_geometry(polygon)) == 1
    assert len(parse_polygon_geometry(multipolygon)) == 1


def test_urban_context_reports_water_roof_and_buildings():
    buildings = (
        BuildingFootprint(
            "b1",
            (_square(39.0, 51.0),),
            area_m2=1000.0,
        ),
    )
    water = (
        WaterFeature(
            "w1",
            (_square(39.002, 51.0, 0.0003),),
            water_class="river",
            subtype="inland",
        ),
    )
    context = UrbanContext(buildings, water)

    building_segment = (
        Point(38.9995, 51.0),
        Point(39.0005, 51.0),
    )
    water_segment = (
        Point(39.0015, 51.0),
        Point(39.0025, 51.0),
    )

    building_metrics = context.segment_metrics(building_segment)
    water_metrics = context.segment_metrics(water_segment)

    assert building_metrics["building_count"] == 1.0
    assert building_metrics["roof_share"] > 0.0
    assert water_metrics["water_share"] > 0.0


def test_urban_construction_multiplier_matches_reference_mode_scope():
    buildings = (
        BuildingFootprint("b1", (_square(39.0, 51.0),), 2000.0),
    )
    water = (
        WaterFeature("w1", (_square(39.001, 51.0, 0.0003),), "river", "inland"),
    )
    context = UrbanContext(buildings, water)
    geometry = LineString((
        Point(38.9995, 51.0),
        Point(39.0005, 51.0),
    )).points

    metro_multiplier, _ = context.construction_multiplier(
        "metro",
        "reserved",
        geometry,
        cost_per_km=14.0,
    )
    bus_multiplier, _ = context.construction_multiplier(
        "bus",
        "mixed",
        geometry,
        cost_per_km=5.0,
    )

    assert metro_multiplier >= 1.0
    assert bus_multiplier == 1.0
