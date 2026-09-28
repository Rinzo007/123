from transit_planner.routing import Journey, JourneyLeg, pareto_filter_journeys


def make_journey(
    *,
    duration_min: float,
    transfers: int,
    in_vehicle: float,
    wait_min: float = 0.0,
    walk_min: float = 0.0,
) -> Journey:
    legs: list[JourneyLeg] = []
    if walk_min > 0:
        legs.append(JourneyLeg("walk", "o", "a", walk_min))
    if in_vehicle > 0:
        legs.append(
            JourneyLeg(
                "transit",
                "a",
                "b",
                in_vehicle,
                "r1",
                wait_min=wait_min,
                service_id="s1",
            )
        )
    if transfers > 0:
        legs.append(JourneyLeg("walk", "b", "c", 3.0))
        legs.append(
            JourneyLeg("transit", "c", "d", max(0.0, in_vehicle - 3.0), "r2")
        )
    return Journey("o", "d", duration_min, transfers, tuple(legs))


def perceived(journey: Journey) -> float:
    in_vehicle = sum(leg.duration_min for leg in journey.legs if leg.kind == "transit")
    wait = sum(leg.wait_min for leg in journey.legs if leg.kind == "transit")
    walk = sum(leg.duration_min for leg in journey.legs if leg.kind == "walk")
    return in_vehicle + 1.65 * walk + 1.72 * wait + 6.75 * journey.transfers


def test_pareto_filter_removes_dominated_journey() -> None:
    best = make_journey(duration_min=20.0, transfers=0, in_vehicle=20.0)
    worse = make_journey(duration_min=25.0, transfers=1, in_vehicle=22.0)

    kept = pareto_filter_journeys((best, worse), perceived_time=perceived)

    assert kept == (best,)


def test_pareto_filter_keeps_tradeoff_journey() -> None:
    # Fewer transfers but worse perceived time: not dominated.
    fast_many = make_journey(duration_min=20.0, transfers=2, in_vehicle=20.0)
    slow_few = make_journey(duration_min=30.0, transfers=0, in_vehicle=30.0)

    kept = pareto_filter_journeys((fast_many, slow_few), perceived_time=perceived)

    assert set(kept) == {fast_many, slow_few}


def test_pareto_filter_keeps_identical_metric_tuples() -> None:
    first = make_journey(duration_min=20.0, transfers=0, in_vehicle=20.0)
    second = make_journey(duration_min=20.0, transfers=0, in_vehicle=20.0)

    kept = pareto_filter_journeys((first, second), perceived_time=perceived)

    assert len(kept) == 2


def test_pareto_filter_single_and_empty() -> None:
    only = make_journey(duration_min=10.0, transfers=0, in_vehicle=10.0)
    assert pareto_filter_journeys((), perceived_time=perceived) == ()
    assert pareto_filter_journeys((only,), perceived_time=perceived) == (only,)


def test_pareto_filter_chain_dominance() -> None:
    best = make_journey(duration_min=10.0, transfers=0, in_vehicle=10.0)
    middle = make_journey(duration_min=15.0, transfers=1, in_vehicle=15.0)
    worst = make_journey(duration_min=20.0, transfers=2, in_vehicle=20.0)

    kept = pareto_filter_journeys((worst, middle, best), perceived_time=perceived)

    assert kept == (best,)
