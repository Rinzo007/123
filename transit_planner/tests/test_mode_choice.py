from transit_planner.mode_choice import ModeChoiceConfig, probabilities, utilities


def test_logit_probabilities_sum_to_one():
    values = utilities(
        walk_time_min=30,
        car_time_min=15,
        transit_time_min=20,
        config=ModeChoiceConfig(scale=0.1),
    )
    probs = probabilities(values)
    assert abs(sum(probs.values()) - 1.0) < 1e-12
    assert probs["car"] > probs["walk"]


def test_unavailable_transit_gets_zero_probability():
    values = utilities(
        walk_time_min=10,
        car_time_min=20,
        transit_time_min=None,
    )
    assert probabilities(values)["transit"] == 0.0
