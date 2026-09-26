from transit_planner.data import GeoJSONRoadProvider
from transit_planner.providers import CityDataProvider


def test_geojson_provider_ignores_non_lines():
    provider = GeoJSONRoadProvider(
        {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]}},
                {
                    "type": "Feature",
                    "id": "r1",
                    "properties": {"maxspeed": 40, "highway": "primary", "length_m": 123.5},
                    "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 0]]},
                },
            ],
        }
    )
    roads = provider.load_roads()
    assert len(roads) == 1
    assert roads[0].id == "r1"
    assert roads[0].speed_kph == 40
    assert roads[0].length_m == 123.5


def test_composable_city_provider():
    dataset = CityDataProvider().load()
    assert dataset.roads == ()
