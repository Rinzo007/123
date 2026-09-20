"""Golden/regression checks for the Takt-compatible passenger-flow core."""

from __future__ import annotations

import json
import math
import sys
import types
from pathlib import Path

try:
    import models as _models_stub
except ImportError:
    _models_stub = types.ModuleType("models")
    _models_stub.RouteLike = object
    sys.modules["models"] = _models_stub

try:
    import support as _support_stub
except ImportError:
    _support_stub = types.ModuleType("support")
    _support_stub.type_label = lambda value: str(value)
    sys.modules["support"] = _support_stub

import numpy as np

from passenger_flow.algorithm.mode_choice import (
    _takt_mode_shares,
    _takt_no_car_shares,
    _takt_route_probs,
)
from passenger_flow.base.models import ModeChoiceConfig
from passenger_flow.base.takt import (
    _takt_fare_eur,
    _takt_hold_prob,
    _takt_po_seconds,
)
from passenger_flow.network.routes import _direct_journeys, _route_ride_time_min


FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "takt_parity.json").read_text(
        encoding="utf-8"
    )
)


def test_takt_defaults_are_the_reference_defaults() -> None:
    mode = ModeChoiceConfig()
    assert math.isclose(mode.car_no_car_share, 0.35)
    assert math.isclose(mode.car_no_car_factor, 0.78)
    assert math.isclose(mode.walk_speed_mps, 1.33)
    assert math.isclose(mode.walk_circuity, 1.25)
    assert math.isclose(mode.rider_bias_s, 0.0)


def test_takt_wait_and_fare_golden_values() -> None:
    wait = FIXTURE["wait"]
    assert [
        _takt_po_seconds(h) for h in wait["headways_min"]
    ] == wait["expected_seconds"]

    fare = FIXTURE["fare"]
    got = [_takt_fare_eur(0.6, 0.12, d) for d in fare["dist_m"]]
    assert np.allclose(got, fare["expected_eur"], rtol=1e-12, atol=1e-12)


def test_takt_hold_and_route_probability_golden_values() -> None:
    hold = FIXTURE["hold_probability"]
    assert math.isclose(
        _takt_hold_prob(hold["wait_s"], hold["halfwidth_s"]),
        hold["expected"],
        rel_tol=1e-12,
        abs_tol=1e-12,
    )

    routes = FIXTURE["route_probabilities"]
    got = _takt_route_probs(np.asarray(routes["costs_s"], dtype=np.float64))
    assert np.allclose(got, routes["expected"], rtol=1e-12, atol=1e-12)


def test_density_adjusted_no_car_shares_match_takt_formula() -> None:
    case = FIXTURE["no_car_density"]
    mode = ModeChoiceConfig()
    got = _takt_no_car_shares(
        mode, np.asarray(case["population"], dtype=np.float64)
    )
    assert got is not None
    assert np.allclose(got, case["expected"], rtol=1e-7, atol=1e-8)
    assert math.isclose(
        float(np.average(got, weights=case["population"])),
        mode.car_no_car_share * mode.car_no_car_factor,
        rel_tol=1e-12,
        abs_tol=1e-12,
    )


def test_mode_choice_golden_value_and_sum() -> None:
    case = FIXTURE["mode_choice"]
    got = _takt_mode_shares(
        ModeChoiceConfig(),
        case["od_meters"],
        case["transit_s"],
        case["fare_eur"],
    )
    assert np.allclose(got, case["expected"], rtol=1e-10, atol=1e-12)
    assert math.isclose(sum(got), 1.0, rel_tol=1e-12, abs_tol=1e-12)


def test_explicit_cumt_drives_direct_journey_time() -> None:
    case = FIXTURE["route_timing"]
    sequence = {
        "stops": [
            {"position": 0, "lat": 52.0, "lon": 4.0},
            {"position": 1, "lat": 52.01, "lon": 4.01},
            {"position": 2, "lat": 52.02, "lon": 4.02},
        ],
        "cum_t_s": list(case["cum_t_s"]),
        "dwell_s": case["dwell_s"],
        "cycle_run_s": case["cum_t_s"][-1],
        "closed": False,
        "both_ways": False,
    }
    got = _route_ride_time_min(sequence, case["from_pos"], case["to_pos"])
    assert math.isclose(got, case["expected_min"], rel_tol=1e-12, abs_tol=1e-12)

    journeys = _direct_journeys(
        {0: (2, 0, 2)},
        [sequence],
        stop_time_min=2.0,
        walk_to_stop_min=0.0,
        wait_time_min=0.0,
        seq_headway_min={0: 15.0},
        wait_calc="takt",
    )
    assert len(journeys) == 1
    assert math.isclose(journeys[0][0], case["expected_min"] + 7.5, rel_tol=1e-12, abs_tol=1e-12)
