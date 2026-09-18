import json

import numpy as np
from shapely.geometry import Point, Polygon

from od.builders import _sparse_gravity_seed
from od.zones.source import assign_district_names, load_districts


def test_load_districts_parses_geojson_geometry(tmp_path):
    path = tmp_path / "districts.geojson"
    path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"name": "Центр"},
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [
                                [
                                    [37.0, 54.0],
                                    [37.1, 54.0],
                                    [37.1, 54.1],
                                    [37.0, 54.1],
                                    [37.0, 54.0],
                                ]
                            ],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    places = load_districts(path)
    assert places[0][0] == "Центр"
    assert isinstance(places[0][1], Polygon)


def test_assign_district_names_uses_geometry_objects():
    from od.model import Zones

    zones = Zones(
        ids=np.array([1], dtype=np.int64),
        polygons=(Polygon([(37.04, 54.04), (37.06, 54.04), (37.06, 54.06), (37.04, 54.06)]),),
        xy=np.array([[37.05, 54.05]]),
        bounds=(37.04, 54.04, 37.06, 54.06),
    )
    names = assign_district_names(zones, [("Центр", Point(37.05, 54.05))])
    assert names == ("Центр",)


def test_sparse_gravity_seed_does_not_require_dense_kernel_copy():
    production = np.array([10.0, 20.0, 30.0])
    attraction = np.array([30.0, 20.0, 10.0])
    impedance = np.array(
        [
            [0.0, 1.0, 100.0],
            [1.0, 0.0, 2.0],
            [100.0, 2.0, 0.0],
        ]
    )

    seed = _sparse_gravity_seed(
        production,
        attraction,
        beta=1.0,
        impedance=impedance,
    )
    assert seed.shape == impedance.shape
    assert seed[0, 0] == 0.0
    assert seed[0, 2] == 0.0
    assert seed[1, 2] > 0.0
