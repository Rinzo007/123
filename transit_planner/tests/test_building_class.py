import pytest

from transit_planner.building_class import (
    BuildingClass,
    ExternalSignals,
    classify_building,
    classification_summary,
)
from transit_planner.control_totals import ZoneControl, anchor_jobs_to_control, anchor_to_control
from transit_planner.geo import Point
from transit_planner.urban import BuildingFootprint


def _building(**kwargs) -> BuildingFootprint:
    defaults = {
        "id": "b1",
        "polygons": ((Point(0, 0), Point(1, 0), Point(1, 1), Point(0, 1)),),
        "area_m2": 100.0,
        "building_class": None,
        "subtype": None,
        "num_floors": 2.0,
    }
    defaults.update(kwargs)
    return BuildingFootprint(**defaults)


def test_operator_override_beats_everything():
    building = _building(building_class="apartments", area_m2=9000.0, num_floors=8.0)
    signals = ExternalSignals(override_class={"b1": BuildingClass.INERT})
    result = classify_building(building, signals=signals)
    assert result.building_class is BuildingClass.INERT
    assert result.decided_by == "operator_override"
    assert result.confidence == 1.0
    # Дальше каскад не пошёл: об этом говорит единственный сигнал в следе.
    assert [outcome.name for outcome in result.outcomes] == ["operator_override"]


def test_osm_subtag_outranks_overture_class():
    signals = ExternalSignals(osm_tag_class={"b1": BuildingClass.INERT})
    result = classify_building(_building(building_class="apartments"), signals=signals)
    assert result.building_class is BuildingClass.INERT
    assert result.decided_by == "osm_subtag"
    # Решение принято раньше класса Overture, поэтому класс не смотрели.
    assert not any(outcome.name == "overture_class" for outcome in result.outcomes)


def test_overture_class_decides_when_present():
    result = classify_building(_building(building_class="apartments"))
    assert result.building_class is BuildingClass.RESIDENTIAL
    assert result.decided_by == "overture_class"
    assert result.confidence > 0.8


def test_office_is_workplace_not_residential():
    result = classify_building(_building(building_class="office"))
    assert result.building_class is BuildingClass.WORKPLACE


def test_roof_is_inert():
    """roof - это контур кровли, а не здание; население из него брать нельзя."""
    assert classify_building(_building(building_class="roof")).building_class is BuildingClass.INERT


def test_subtype_used_when_class_missing():
    result = _building(building_class=None, subtype="residential")
    assert classify_building(result).building_class is BuildingClass.RESIDENTIAL
    assert classify_building(result).decided_by == "overture_subtype"


def test_unclassed_building_falls_through_to_morphology():
    building = _building(building_class=None, subtype=None, area_m2=120.0, num_floors=2.0)
    result = classify_building(building)
    assert result.building_class is BuildingClass.RESIDENTIAL
    assert result.decided_by == "morphology"
    # След содержит все опробованные уровни, а не только победивший: по нему
    # видно, что классификатор смотрел в OSM и растр и там ничего не нашёл.
    names = [outcome.name for outcome in result.outcomes]
    assert names == ["operator_override", "osm_subtag", "osm_landuse",
                     "urban_landuse_raster", "morphology"]
    assert all(outcome.decision is None for outcome in result.outcomes[:-1])
    assert result.outcomes[-1].reason


def test_large_multistorey_without_tags_reads_as_workplace():
    building = _building(building_class=None, subtype=None, area_m2=4000.0, num_floors=6.0)
    assert classify_building(building).building_class is BuildingClass.WORKPLACE


def test_large_lowrise_without_tags_stays_unknown():
    """Крупный одноэтажный корпус - это склад или ТЦ, не жильё и не вывод."""
    building = _building(building_class=None, subtype=None, area_m2=6000.0, num_floors=1.0)
    result = classify_building(building)
    assert result.building_class is BuildingClass.UNKNOWN
    assert result.confidence < 0.2


def test_tiny_contour_is_unknown():
    building = _building(building_class=None, subtype=None, area_m2=3.0)
    assert classify_building(building).building_class is BuildingClass.UNKNOWN


def test_landuse_and_raster_take_part_when_present():
    building = _building(building_class=None, subtype=None, area_m2=500.0, num_floors=1.0)
    landuse = ExternalSignals(landuse_class={"b1": BuildingClass.RESIDENTIAL})
    assert classify_building(building, signals=landuse).decided_by == "osm_landuse"
    raster = ExternalSignals(urban_raster_class={"b1": BuildingClass.WORKPLACE})
    assert classify_building(building, signals=raster).decided_by == "urban_landuse_raster"


def test_summary_reports_absent_signals_and_unknown_area():
    buildings = (
        _building(id="a", building_class="apartments"),
        _building(id="b", building_class=None, subtype=None, area_m2=6000.0, num_floors=1.0),
    )
    summary = classification_summary(tuple(classify_building(b) for b in buildings))
    assert summary["buildings"] == 2
    assert summary["byTier"] == {"overture_class": 1, "morphology": 1}
    assert summary["byClass"]["unknown"] == 1
    assert summary["unknownFloorAreaM2"] == pytest.approx(6000.0)
    # Незагруженные источники перечислены, а не выданы за сработавшие.
    assert set(summary["absentSignals"]) == {"osm_tag", "osm_landuse", "urban_landuse_raster"}


def test_classification_is_serialisable_for_provenance():
    payload = classify_building(_building(building_class="office")).to_dict()
    assert payload["class"] == "workplace"
    assert payload["decidedBy"] == "overture_class"
    assert isinstance(payload["signals"], list)


def test_control_totals_scale_estimates_without_changing_shares():
    estimates = {"z1": 30.0, "z2": 10.0}
    controls = (ZoneControl("z1", 600.0, source="census", vintage="2021"),
                ZoneControl("z2", 200.0, source="census", vintage="2021"))
    anchored, report = anchor_to_control(estimates, controls)
    assert anchored == {"z1": 600.0, "z2": 200.0}
    assert report.factor == pytest.approx(20.0)
    assert report.method == "scale_to_control_total"
    assert report.zones_zero_estimate_but_control == ()


def test_zone_without_estimate_is_reported_not_hidden():
    estimates = {"z1": 50.0}
    controls = (ZoneControl("z1", 500.0), ZoneControl("z2", 500.0))
    anchored, report = anchor_to_control(estimates, controls)
    # Итог сошёлся с контролем, но зона без оценки осталась пустой - и это видно.
    assert sum(anchored.values()) == pytest.approx(1000.0)
    assert report.zones_zero_estimate_but_control == ("z2",)
    assert report.total_control == pytest.approx(1000.0)


def test_estimate_where_control_is_zero_is_reported():
    estimates = {"z1": 10.0, "z9": 90.0}
    controls = (ZoneControl("z1", 100.0),)
    anchored, report = anchor_to_control(estimates, controls)
    assert report.zones_zero_control_but_estimate == ("z9",)
    assert anchored["z9"] > 0.0


def test_zero_estimates_do_not_divide_by_zero():
    anchored, report = anchor_to_control({}, (ZoneControl("z1", 100.0),))
    assert anchored == {}
    assert report.factor == 0.0
    assert report.total_estimated == 0.0


def test_negative_values_are_rejected():
    with pytest.raises(ValueError):
        anchor_to_control({"z1": -1.0}, (ZoneControl("z1", 10.0),))
    with pytest.raises(ValueError):
        anchor_to_control({"z1": 1.0}, (ZoneControl("z1", -10.0),))


def test_jobs_anchor_uses_jobs_column():
    estimates = {"z1": 25.0, "z2": 75.0}
    controls = (ZoneControl("z1", 900.0, jobs=1.0), ZoneControl("z2", 0.0, jobs=3.0))
    anchored, report = anchor_jobs_to_control(estimates, controls)
    assert report.total_control == pytest.approx(4.0)
    assert anchored == {"z1": 1.0, "z2": 3.0}
    assert report.method == "scale_to_control_total"


def test_anchoring_report_is_serialisable():
    _, report = anchor_to_control({"z1": 1.0}, (ZoneControl("z1", 2.0),))
    payload = report.to_dict()
    assert payload["totalControl"] == 2.0
    assert "zonesZeroEstimateButControl" in payload
