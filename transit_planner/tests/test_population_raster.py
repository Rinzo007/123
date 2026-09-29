import json

import pytest

from transit_planner.geo import Point
from transit_planner.population_raster import (
    GHS_POP_NODATA,
    PopulationRaster,
    find_population_raster,
    population_by_zone,
    read_population_cells,
)


def _write_raster(tmp_path, values, name="rus_pop_test.tif"):
    rasterio = pytest.importorskip("rasterio")
    import numpy as np

    path = tmp_path / name
    transform = rasterio.Affine(0.001, 0.0, 41.0, 0.0, -0.001, 53.0)
    with rasterio.open(
        path, "w", driver="GTiff", height=values.shape[0], width=values.shape[1],
        count=1, dtype="float32", crs="EPSG:4326", transform=transform,
        nodata=GHS_POP_NODATA,
    ) as dataset:
        dataset.write(values.astype("float32"), 1)
    return path


def test_reads_people_per_cell_and_keeps_nodata_out(tmp_path):
    import numpy as np

    values = np.array([[10.0, 0.0], [20.0, 5.0]])
    values[0, 1] = GHS_POP_NODATA
    path = _write_raster(tmp_path, values)
    read = read_population_cells((52.998, 41.0, 53.002, 41.002),
                                 PopulationRaster(path=path))
    # 10 + 20 + 5 = 35; ячейка с nodata не считается нулём, а пропуском.
    assert read.total_people == pytest.approx(35.0)
    assert read.cells_with_data == 3
    assert len(read.cells) == 3
    assert read.max_people_per_cell == pytest.approx(20.0)
    assert read.coverage == pytest.approx(0.75)


def test_empty_window_reports_zero_not_error(tmp_path):
    import numpy as np

    path = _write_raster(tmp_path, np.full((4, 4), GHS_POP_NODATA))
    # Окно внутри растра, но данных в нём нет: ноль и пропуск, а не ошибка.
    read = read_population_cells((52.997, 41.0, 53.0, 41.004),
                                 PopulationRaster(path=path))
    assert read.total_people == 0.0
    assert read.cells == ()
    assert read.cells_in_window > 0
    assert read.coverage == 0.0


def test_window_outside_raster_is_empty(tmp_path):
    import numpy as np

    path = _write_raster(tmp_path, np.ones((4, 4), dtype="float32"))
    read = read_population_cells((10.0, 10.0, 10.1, 10.1),
                                 PopulationRaster(path=path))
    # Окно целиком снаружи: пусто без ошибки, а не падение на доступе к пикселям.
    assert read.cells == ()
    assert read.total_people == 0.0
    assert read.cells_with_data == 0


def test_fingerprint_is_auditable(tmp_path):
    import numpy as np

    path = _write_raster(tmp_path, np.ones((4, 4), dtype="float32"))
    payload = PopulationRaster(path=path).fingerprint()
    json.dumps(payload)
    assert payload["epoch"] == "2030"
    assert "population" in payload["kind"]
    # В provenance сказано, что тег канала врёт: это важно, иначе следующий
    # читатель снова решит по нему, что это не население.
    assert "built_S_NRES" in payload["kind"]
    assert len(payload["headSha256"]) == 64


def test_explicit_path_wins_over_search(tmp_path, monkeypatch):
    import numpy as np

    path = _write_raster(tmp_path, np.ones((4, 4), dtype="float32"))
    monkeypatch.setattr(
        "transit_planner.population_raster.GHS_POP_SUFFIX", "nothing_matches_"
    )
    assert find_population_raster(explicit=path) == path


def test_raster_is_chosen_by_coverage_not_alphabet(tmp_path, monkeypatch):
    """В каталоге десятки стран; первый по алфавиту увёл бы читателя не туда."""
    import numpy as np
    import rasterio

    # "arm" идёт раньше "rus" по алфавиту. Его растр лежит в Армении и нужную
    # область не покрывает - выбирать его нельзя даже первым по имени.
    arm = _write_raster(tmp_path, np.ones((4, 4), dtype="float32"), name="arm_pop_test.tif")
    with rasterio.open(arm, "r+") as dataset:
        dataset.transform = rasterio.Affine(0.001, 0.0, 45.0, 0.0, -0.001, 40.0)
    near = _write_raster(tmp_path, np.ones((4, 4), dtype="float32"),
                         name="rus_pop_test.tif")

    monkeypatch.setattr("transit_planner.population_raster.SEARCH_DIRS", (str(tmp_path),))
    chosen = find_population_raster(bbox=(52.9995, 41.0005, 53.0005, 41.0015))
    assert chosen == near


def test_raster_outside_area_is_not_chosen(tmp_path, monkeypatch):
    import numpy as np
    import rasterio

    arm = _write_raster(tmp_path, np.ones((4, 4), dtype="float32"), name="arm_pop_test.tif")
    with rasterio.open(arm, "r+") as dataset:
        dataset.transform = rasterio.Affine(0.001, 0.0, 45.0, 0.0, -0.001, 40.0)
    far = _write_raster(tmp_path, np.ones((4, 4), dtype="float32"),
                        name="rus_pop_test.tif")
    with rasterio.open(far, "r+") as dataset:
        dataset.transform = rasterio.Affine(0.001, 0.0, 10.0, 0.0, -0.001, 1.0)
    monkeypatch.setattr("transit_planner.population_raster.SEARCH_DIRS", (str(tmp_path),))
    # Ни один растр не покрывает область - значит и выбирать нечего.
    assert find_population_raster(bbox=(52.9995, 41.0005, 53.0005, 41.0015)) is None


class _Zone:
    def __init__(self, zone_id, x, y):
        self.id = zone_id
        self.centroid_x = x
        self.centroid_y = y


def test_population_lands_in_nearest_zone():
    from transit_planner.population_raster import PopulationCell, PopulationRead

    zones = (_Zone("a", 0.0, 0.0), _Zone("b", 2000.0, 0.0))
    read = PopulationRead(
        cells=(PopulationCell(location=Point(41.0, 53.0), people=100.0),),
        total_people=100.0,
    )
    totals = population_by_zone(read, zones, origin_lon=41.0, origin_lat=53.0)
    assert totals == {"a": pytest.approx(100.0)}


def test_no_cells_means_no_zones():
    from transit_planner.population_raster import PopulationRead

    assert population_by_zone(PopulationRead(), (_Zone("a", 0.0, 0.0),),
                              origin_lon=0.0, origin_lat=0.0) == {}
