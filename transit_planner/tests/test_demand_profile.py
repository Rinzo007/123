from transit_planner.demand import DemandMatrix, ODPairDemand, expand_daily_demand
from transit_planner.demand_profile import DEFAULT_TEMPORAL_DEMAND_PROFILE, TripPurpose


def test_default_temporal_profile_has_five_periods():
    assert len(DEFAULT_TEMPORAL_DEMAND_PROFILE.period_ids) == 5
    assert DEFAULT_TEMPORAL_DEMAND_PROFILE.period_ids == (
        "night",
        "morning_peak",
        "daytime",
        "evening_peak",
        "late_evening",
    )


def test_expand_daily_all_purpose_preserves_total_demand():
    demand = DemandMatrix((
        ODPairDemand("a", "b", 100.0),
    ))

    temporal = expand_daily_demand(demand)

    assert abs(temporal.total_trips - 100.0) < 1e-9
    assert len(temporal.by_period("morning_peak")) > 0
    assert len(temporal.by_purpose(TripPurpose.WORK.value)) == 5


def test_expand_daily_explicit_purpose_uses_purpose_period_profile():
    demand = DemandMatrix((
        ODPairDemand("a", "b", 100.0, purpose=TripPurpose.WORK.value),
    ))

    temporal = expand_daily_demand(demand)

    expected = 100.0 * 0.35
    actual = sum(
        pair.trips
        for pair in temporal.by_period("morning_peak")
    )
    assert abs(actual - expected) < 1e-9
    assert temporal.by_purpose("work")[0].purpose == "work"


def test_purpose_profile_shares_sum_to_one():
    for profile in DEFAULT_TEMPORAL_DEMAND_PROFILE.purposes:
        assert abs(sum(profile.period_shares.values()) - 1.0) < 1e-9
    assert abs(sum(profile.daily_share for profile in DEFAULT_TEMPORAL_DEMAND_PROFILE.purposes) - 1.0) < 1e-9
