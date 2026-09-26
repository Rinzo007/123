from transit_planner.city import DemandZone
from transit_planner.demand_profile import TripPurpose
from transit_planner.geo import Point
from transit_planner.od import purpose_gravity_od
from transit_planner.places import CityPlace, PlacePurposeMapper, aggregate_place_attractions


def test_places_are_aggregated_to_nearest_zone_by_purpose():
    zones = (
        DemandZone("a", 0, 0, population=1000),
        DemandZone("b", 1000, 0, population=1000),
    )
    places = (
        CityPlace(
            "school",
            "School",
            Point(900, 0),
            basic_category="school",
            importance=2.0,
        ),
        CityPlace(
            "mall",
            "Mall",
            Point(100, 0),
            basic_category="shopping_mall",
            importance=3.0,
        ),
    )

    result = aggregate_place_attractions(zones, places)
    assert result[1].attractions["education"] == 2.0
    assert result[0].attractions["shopping"] == 3.0


def test_purpose_gravity_od_produces_purpose_labeled_daily_demand():
    zones = (
        DemandZone(
            "a",
            0,
            0,
            population=1000,
            purpose_attractions=(("education", 100.0),),
        ),
        DemandZone(
            "b",
            1000,
            0,
            population=500,
            jobs=200.0,
            purpose_attractions=(("education", 300.0),),
        ),
    )

    demand = purpose_gravity_od(
        zones,
        trip_rate=0.1,
        decay=0.01,
    )

    assert demand.total_trips_per_day > 0
    purposes = {pair.purpose for pair in demand.pairs}
    assert TripPurpose.EDUCATION.value in purposes


def test_purpose_gravity_work_uses_jobs_when_no_explicit_attraction():
    zones = (
        DemandZone("a", 0, 0, population=1000, jobs=10),
        DemandZone("b", 1000, 0, population=500, jobs=1000),
    )

    demand = purpose_gravity_od(
        zones,
        trip_rate=0.1,
    )

    work_destinations = {
        pair.destination_zone_id
        for pair in demand.pairs
        if pair.purpose == TripPurpose.WORK.value
    }
    assert "b" in work_destinations
