"""Golden/regression checks for the Takt-compatible passenger-flow core."""
# P6 synchronization: keep CI execution tied to the latest parity fixes.

from __future__ import annotations

import hashlib
import json
import math
import os
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
import pytest

from passenger_flow.algorithm.assign import _journey_crowd_extra
from passenger_flow.algorithm.kpis import (
    _atomic_infrastructure_sections,
    _build_line_kpis,
    _sequence_capital_cost_eur,
    _shared_capacity_min_headways,
    _period_service_metrics,
    _geometry_reuse_edges,
    _geometry_segment_edges,
)
from passenger_flow.core import _takt_car_period_multipliers, _validate_base_time, _validate_flow_inputs, _validate_route_sequences, _validate_sparse_od
from passenger_flow.algorithm.wait import _build_crowd_state, _takt_msa_gap
from passenger_flow.algorithm.mode_choice import (
    _takt_car_cost_s,
    _takt_car_period_multiplier,
    _takt_mode_shares,
    _takt_no_car_shares,
    _takt_route_probs,
)
from passenger_flow.base.models import ModeChoiceConfig, PassengerFlowError
from passenger_flow.base.takt import (
    _takt_fare_eur,
    _takt_hold_prob,
    _takt_po_seconds,
)
from passenger_flow.network.geometry import haversine_meters
from scripts.takt_differential import compare_snapshots
from passenger_flow.algorithm.assign import _takt_co_route_probs, _takt_leg_choice_probs
from passenger_flow.network.routes import (
    JourneyAlternative,
    _direct_journeys,
    _build_transfer_edge_index,
    _cached_ride_edge_time_min,
    _ride_edge_time_min,
    _route_ride_time_min,
    _takt_ri_access_min,
    _takt_ri_anchor_min,
    build_journeys,
    _build_route_stop_sequence,
)


def _repo_root() -> Path:
    workspace = os.environ.get("GITHUB_WORKSPACE")
    candidates = [Path(workspace)] if workspace else []
    candidates.extend([Path.cwd(), Path(__file__).resolve().parents[1]])
    for root in candidates:
        if root and (root / "scripts" / "bd956ff0a1875604740f.js").is_file():
            return root
    return Path.cwd()


FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "takt_parity.json").read_text(
        encoding="utf-8"
    )
)


def test_core_additional_options_enabled_by_default() -> None:
    import inspect

    from passenger_flow.core import run_passenger_flow
    from passenger_flow.base.takt import TAKT_PERIODS

    sig = inspect.signature(run_passenger_flow)
    assert sig.parameters["max_transfers"].default == 3
    assert sig.parameters["headway_min"].default == 10.0
    assert sig.parameters["wait_crowding_per_100_min"].default == 0.1
    assert sig.parameters["periods"].default == TAKT_PERIODS
    assert sig.parameters["include_reliability"].default is True
    assert sig.parameters["msa_max_iterations"].default == 20
    assert sig.parameters["msa_gap"].default == 0.01


def test_od_uses_one_takt_model() -> None:
    import inspect

    import od
    from od.builders import build_purpose_od

    assert not hasattr(od, "build_gravity_od")
    assert "engine" not in inspect.signature(build_purpose_od).parameters


def test_period_validation_rejects_non_finite_coefficients() -> None:
    from passenger_flow.base.models import Period
    from passenger_flow.core import _validate_periods
    with pytest.raises(PassengerFlowError):
        _validate_periods((Period(key="x", label="x", out=float("nan"), ret=1.0),))


def test_sparse_od_validation_rejects_bad_shape_and_values() -> None:
    class SparseStub:
        shape = (2, 3)
        data = np.asarray([1.0], dtype=np.float64)
    with pytest.raises(PassengerFlowError):
        _validate_sparse_od(SparseStub(), 2)

    class SparseBadData:
        shape = (2, 2)
        data = np.asarray([float("inf")], dtype=np.float64)
    with pytest.raises(PassengerFlowError):
        _validate_sparse_od(SparseBadData(), 2)

def test_mode_choice_bad_numeric_type_is_wrapped_in_passenger_flow_error() -> None:
    from passenger_flow.core import _validate_mode_choice

    from dataclasses import replace

    mode = replace(ModeChoiceConfig(), car_no_car_share="not-a-number")
    with pytest.raises(PassengerFlowError):
        _validate_mode_choice(mode)


def test_flow_input_validation_rejects_non_finite_scalars() -> None:
    from passenger_flow.base.takt import TAKT_PERIODS
    kwargs = dict(
        max_transfers=3,
        transfer_radius_m=800.0,
        transfer_wait_min=None,
        transfer_penalty_calc="takt",
        headway_min=10.0,
        headway_by_route=None,
        wait_calc="takt",
        include_reliability=True,
        capex_factor=1.0,
        capex_amort_years=30.0,
        wait_crowding_per_100_min=0.1,
        msa_max_iterations=20,
        msa_gap=0.01,
        periods=TAKT_PERIODS,
        mode_choice=ModeChoiceConfig(),
        base_time_s=None,
        stop_search_radius_m=1500.0,
        stop_time_min=2.0,
        wait_time_min=0.0,
        walk_to_stop_min=0.0,
        transfer_penalty_min=10.0,
        logit_temp=10.0,
    )
    with pytest.raises(PassengerFlowError):
        _validate_flow_inputs(np.zeros((1, 1)), 1, headway_min=float("nan"), **{k: v for k, v in kwargs.items() if k != "headway_min"})
    with pytest.raises(PassengerFlowError):
        _validate_flow_inputs(np.zeros((1, 1)), 1, transfer_radius_m=float("inf"), **{k: v for k, v in kwargs.items() if k != "transfer_radius_m"})


def test_route_sequence_validation_rejects_invalid_runtime_fields() -> None:
    seq = _synthetic_sequence([1, 2, 3])
    seq["speed_kmh"] = float("nan")
    with pytest.raises(PassengerFlowError):
        _validate_route_sequences([seq])

    seq = _synthetic_sequence([1, 2, 3])
    seq["open_pre"] = [0, 1]
    with pytest.raises(PassengerFlowError):
        _validate_route_sequences([seq])

    seq = _synthetic_sequence([1, 2, 3])
    seq["segment_time_s"] = (60.0,)
    with pytest.raises(PassengerFlowError):
        _validate_route_sequences([seq])


def test_zone_validation_rejects_non_finite_or_out_of_range_coordinates() -> None:
    from passenger_flow.core import _validate_zones

    class ZonesStub:
        xy = np.asarray([[float("nan"), 52.0]], dtype=np.float64)

        def __len__(self) -> int:
            return 1

    with pytest.raises(PassengerFlowError):
        _validate_zones(ZonesStub())

    class BadBounds:
        xy = np.asarray([[181.0, 52.0]], dtype=np.float64)

        def __len__(self) -> int:
            return 1

    with pytest.raises(PassengerFlowError):
        _validate_zones(BadBounds())


def test_run_rejects_invalid_route_before_spatial_index_build() -> None:
    from types import SimpleNamespace

    from passenger_flow.core import run_passenger_flow

    stops = [
        SimpleNamespace(id=1, name="A", latitude=52.0, longitude=4.0),
        SimpleNamespace(id=2, name="B", latitude=float("nan"), longitude=4.01),
    ]
    direction = SimpleNamespace(name="D", stops=stops)
    route = SimpleNamespace(
        ok=True,
        route_id=901,
        name="broken",
        route_type="bus",
        directions=[direction],
        headways=[10.0, 10.0, 10.0, 10.0, 10.0],
    )

    class ZonesStub:
        xy = np.asarray([[4.0, 52.0]], dtype=np.float64)

        def __len__(self) -> int:
            return 1

    with pytest.raises(PassengerFlowError):
        run_passenger_flow(
            [route],
            np.zeros((1, 1), dtype=np.float64),
            ZonesStub(),
        )


def test_route_sequence_validation_rejects_invalid_geometry() -> None:
    seq = _synthetic_sequence([1, 2, 3])
    seq["stops"][1]["lat"] = float("nan")
    with pytest.raises(PassengerFlowError):
        _validate_route_sequences([seq])

def test_route_sequence_validation_rejects_non_monotonic_cumulative_time() -> None:
    seq = _synthetic_sequence([1, 2, 3])
    seq["cum_t_s"] = [0.0, 120.0, 60.0]
    with pytest.raises(PassengerFlowError):
        _validate_route_sequences([seq])


def test_flow_input_validation_rejects_non_finite_logit_temperature() -> None:
    from passenger_flow.base.takt import TAKT_PERIODS
    kwargs = dict(
        max_transfers=3, transfer_radius_m=800.0, transfer_wait_min=None,
        transfer_penalty_calc="takt", headway_min=10.0, headway_by_route=None,
        wait_calc="takt", include_reliability=True, capex_factor=1.0,
        capex_amort_years=30.0, wait_crowding_per_100_min=0.1,
        msa_max_iterations=20, msa_gap=0.01, periods=TAKT_PERIODS,
        mode_choice=ModeChoiceConfig(), base_time_s=None,
        stop_search_radius_m=1500.0, stop_time_min=2.0, wait_time_min=0.0,
        walk_to_stop_min=0.0, transfer_penalty_min=10.0, logit_temp=float("nan"),
    )
    with pytest.raises(PassengerFlowError):
        _validate_flow_inputs(np.zeros((1, 1)), 1, **kwargs)

def test_takt_ri_access_uses_real_near_distance() -> None:
    assert math.isclose(
        _takt_ri_access_min(100.0, od_distance_m=5000.0, base_time_s=None),
        100.0 * 1.3 / (5.0 / 3.6) * 1.5 / 60.0,
        rel_tol=1e-12,
        abs_tol=1e-12,
    )
    assert math.isclose(_takt_ri_anchor_min(), 420.5 / 60.0, rel_tol=1e-12)


def test_takt_co_route_split_uses_inverse_wait_cost() -> None:
    a = _synthetic_sequence([1, 2, 3]); b = _synthetic_sequence([4, 5, 6])
    a["_seq_idx"] = 0; b["_seq_idx"] = 1
    journeys = [
        JourneyAlternative(10.0, ((0, 0, 2),)),
        JourneyAlternative(10.0, ((1, 0, 2),)),
    ]
    probs = _takt_co_route_probs(
        journeys, [a, b], period_index=0,
        seq_headway_min={0: 10.0, 1: 20.0}, crowd_state=None,
    )
    assert np.allclose(probs, [480.0 / 780.0, 300.0 / 780.0], rtol=1e-12, atol=1e-12)

def test_takt_defaults_are_the_reference_defaults() -> None:
    mode = ModeChoiceConfig()
    assert math.isclose(mode.car_no_car_share, 0.35)
    assert math.isclose(mode.car_no_car_factor, 0.78)
    assert math.isclose(mode.walk_speed_mps, 1.33)
    assert math.isclose(mode.walk_circuity, 1.25)
    assert math.isclose(mode.rider_bias_s, 0.0)


def test_takt_car_period_multiplier_matches_reference_formula() -> None:
    assert math.isclose(_takt_car_period_multiplier(200.0, 200.0), 1.0)
    assert math.isclose(_takt_car_period_multiplier(320.0, 200.0), 1.36)
    assert math.isclose(_takt_car_period_multiplier(1000.0, 200.0), 1.8)
    assert math.isclose(_takt_car_period_multiplier(10.0, 0.0), 1.0)


def test_takt_car_period_multipliers_match_period_profile() -> None:
    from passenger_flow.base.takt import TAKT_PERIODS

    rows = np.asarray([0, 1], dtype=np.int64)
    cols = np.asarray([1, 0], dtype=np.int64)
    vals = np.asarray([1000.0, 1000.0], dtype=np.float64)
    got = _takt_car_period_multipliers(rows, cols, vals, TAKT_PERIODS)
    assert np.allclose(got, [1.0, 1.72, 1.0, 1.375, 1.0], rtol=1e-12, atol=1e-12)

def test_takt_car_cost_uses_base_time_and_period_multiplier() -> None:
    mode = ModeChoiceConfig()
    got = _takt_car_cost_s(
        mode,
        10000.0,
        road_time_s=1800.0,
        period_multiplier=1.8,
    )
    expected = 1800.0 * 1.8 + 240.0 + (10.0 * 1.3 * 0.25 + 1.5) * 360.0
    assert math.isclose(got, expected, rel_tol=1e-12, abs_tol=1e-12)

def test_takt_reference_bundle_provenance_is_pinned() -> None:
    reference = json.loads(
        (Path(__file__).parent / "fixtures" / "takt_reference_snapshot.json").read_text(encoding="utf-8")
    )
    rel = reference["reference"]["source"]
    expected = reference["reference"]["bundle_git_blob_sha"]
    path = _repo_root() / rel
    data = path.read_bytes()
    actual = hashlib.sha1(
        f"blob {len(data)}\0".encode("ascii") + data
    ).hexdigest()
    assert actual == expected

def test_python_snapshot_matches_canonical_takt_reference() -> None:
    reference = json.loads(
        (Path(__file__).parent / "fixtures" / "takt_reference_snapshot.json").read_text(encoding="utf-8")
    )
    waits = [_takt_po_seconds(6), _takt_po_seconds(12), _takt_po_seconds(15)]
    fares = [_takt_fare_eur(0.6, 0.12, distance) for distance in (0, 10000, 30000)]
    from passenger_flow.base.takt import TAKT_PERIODS
    rows = np.asarray([0, 1], dtype=np.int64)
    cols = np.asarray([1, 0], dtype=np.int64)
    vals = np.asarray([1000.0, 1000.0], dtype=np.float64)
    snapshot = {
        "reference": reference["reference"],
        "tolerance": reference["tolerance"],
        "wait_seconds": waits,
        "fare_eur": fares,
        "hold_probability": _takt_hold_prob(60.0, 90.0),
        "route_probabilities": _takt_route_probs(np.asarray([600.0, 900.0])).tolist(),
        "mode_shares": list(_takt_mode_shares(ModeChoiceConfig(), 5000.0, 1200.0, 1.2)),
        "car_period_multipliers": list(_takt_car_period_multipliers(rows, cols, vals, TAKT_PERIODS)),
        "msa_gap_example": _takt_msa_gap({1: 10.0}, {1: 15.0}, {(1, 0): 2.0}, {(1, 0): 3.0}, {(1, 0): 4.0}, {(1, 0): 5.0}, {(1, 0): 6.0}, {(1, 0): 8.0}),
    }
    assert compare_snapshots(reference, snapshot) == []

def test_msa_period_dense_state_has_no_stale_route_reference(monkeypatch) -> None:
    import passenger_flow.algorithm.wait as wait_module

    calls = []

    def fake_assign(*args, **kwargs):
        calls.append(1)
        return {
            "seg_forward_totals": {(0, 0): 10.0},
            "seg_reverse_totals": {},
            "seq_stop_totals": {(0, 0): 5.0},
        }

    monkeypatch.setattr(wait_module, "_assign_od", fake_assign)
    monkeypatch.setattr(wait_module, "_build_crowd_state", lambda *args, **kwargs: {})

    route_sequences = [{
        "stops": [{"position": 0}, {"position": 1}],
        "closed": False,
    }]
    result, iterations, gap = wait_module._run_msa_period(
        np.asarray([0], dtype=np.int64),
        np.asarray([1], dtype=np.int64),
        np.asarray([10.0], dtype=np.float64),
        {},
        route_sequences,
        stop_time_min=2.0,
        wait_time_min=0.0,
        walk_to_stop_min=0.0,
        transfer_penalty_min=0.0,
        transfer_wait_min=0.0,
        transfer_radius_m=800.0,
        max_transfers=1,
        transfer_penalty_calc="fixed",
        logit_temp=10.0,
        out_factor=1.0,
        ret_factor=0.0,
        mode=None,
        zones=None,
        wait_crowding_per_100_min=0.1,
        reliability_extra=None,
        max_iterations=2,
        gap_tol=0.01,
        seq_headway_min=None,
        seq_jitter_s=None,
        no_car_shares=None,
        wait_calc="takt",
        period_hours=24.0,
        vehicle_specs=None,
        base_time_s=None,
        car_base_time_s=None,
        period_index=0,
        car_period_multiplier=1.0,
        transfer_index={},
        od_distances_m=None,
        ride_edge_cache={},
    )
    assert len(calls) == 2
    assert iterations == 2
    assert math.isclose(gap, 0.0, abs_tol=1e-12)
    assert result["seg_forward_totals"][(0, 0)] == 10.0
    assert result["seq_stop_totals"][(0, 0)] == 5.0


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


def test_msa_gap_uses_smoothing_step_not_raw_to_new_delta() -> None:
    got = _takt_msa_gap(
        {1: 10.0},
        {1: 15.0},
        {(1, 0): 2.0},
        {(1, 0): 3.0},
        {(1, 0): 4.0},
        {(1, 0): 5.0},
        {(1, 0): 6.0},
        {(1, 0): 8.0},
    )
    assert math.isclose(got, 0.25, rel_tol=1e-12, abs_tol=1e-12)

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


def test_route_sequence_caches_segment_movement_times() -> None:
    seq = _synthetic_sequence([1, 2, 3])
    assert seq["segment_time_s"] == (60.0, 60.0)

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


def _synthetic_sequence(ids: list[int]) -> dict:
    return {
        "_seq_idx": 0,
        "route_id": 1,
        "route_name": "synthetic",
        "route_type": "bus",
        "route_type_key": "bus",
        "access_m": 500.0,
        "di": 0,
        "direction_name": "A",
        "stops": [
            {
                "id": stop_id,
                "name": str(stop_id),
                "lat": 52.0 + i * 0.001,
                "lon": 4.0 + i * 0.001,
                "position": i,
            }
            for i, stop_id in enumerate(ids)
        ],
        "cum_t_s": [0.0, 60.0, 120.0],
        "segment_time_s": (60.0, 60.0),
        "cycle_run_s": 120.0,
        "dwell_s": 0.0,
        "speed_kmh": 18.0,
        "closed": False,
        "both_ways": False,
        "phase_s": 0.0,
        "open": [True, True, True],
        "open_pre": [0, 1, 2, 3],
    }


def test_cached_ride_edge_cost_is_identical_to_uncached() -> None:
    seq = _synthetic_sequence([1, 2, 3])
    crowd_state = {
        "seg_forward": {(0, 0): 0.75, (0, 1): 0.50},
        "seg_reverse": {},
        "stop_extra": {},
        "unreliability": {(0, 0): 1.03},
    }
    expected = _ride_edge_time_min(
        seq,
        0,
        2,
        stop_time_min=0.0,
        crowd_state=crowd_state,
    )
    cache: dict[tuple[int, int, int], float] = {}
    first = _cached_ride_edge_time_min(
        [seq],
        0,
        0,
        2,
        stop_time_min=0.0,
        crowd_state=crowd_state,
        cache=cache,
    )
    second = _cached_ride_edge_time_min(
        [seq],
        0,
        0,
        2,
        stop_time_min=0.0,
        crowd_state=crowd_state,
        cache=cache,
    )
    assert math.isclose(first, expected, rel_tol=1e-12, abs_tol=1e-12)
    assert math.isclose(second, expected, rel_tol=1e-12, abs_tol=1e-12)
    assert cache == {(0, 0, 2): expected}


def test_build_journeys_shared_ride_cache_preserves_results() -> None:
    seq = _synthetic_sequence([1, 2, 3])
    kwargs = dict(
        stop_time_min=0.0,
        wait_time_min=0.0,
        walk_to_stop_min=0.0,
        transfer_penalty_min=0.0,
        transfer_wait_min=0.0,
        transfer_radius_m=800.0,
        max_transfers=1,
        transfer_penalty_calc="fixed",
        seq_headway_min=None,
        seq_jitter_s=None,
        wait_calc="takt",
        transfer_index={},
    )
    uncached = build_journeys([(0, 0, 0)], [(0, 2, 2)], [seq], **kwargs)
    cache: dict[tuple[int, int, int], float] = {}
    cached = build_journeys(
        [(0, 0, 0)],
        [(0, 2, 2)],
        [seq],
        ride_edge_cache=cache,
        **kwargs,
    )
    assert cached == uncached
    assert cache


def test_base_t_rest_alternative_is_present_when_base_time_exists() -> None:
    got = _takt_mode_shares(
        ModeChoiceConfig(),
        5000.0,
        1200.0,
        1.2,
        rest_s=1500.0,
    )
    assert len(got) == 5
    assert got[4] > 0.0
    assert math.isclose(sum(got), 1.0, rel_tol=1e-12, abs_tol=1e-12)


def test_multi_leg_search_reaches_four_legs() -> None:
    seqs = [
        _synthetic_sequence([1, 2, 3]),
        _synthetic_sequence([2, 4, 5]),
        _synthetic_sequence([5, 6, 7]),
        _synthetic_sequence([7, 8, 9]),
    ]
    # Keep only the intended adjacent-line transfer stops inside 800 m.
    for idx, (shift, stops) in enumerate((
        (0.00, (0.00, 0.01, 0.02)),
        (0.02, (0.08, 0.09, 0.10)),
        (0.11, (0.08, 0.09, 0.10)),
        (0.20, (0.08, 0.09, 0.10)),
    )):
        for stop, dx in zip(seqs[idx]["stops"], stops):
            stop["lon"] = 4.0 + shift + dx
    # Align the transfer endpoints exactly.
    seqs[1]["stops"][0]["lon"] = seqs[0]["stops"][2]["lon"]
    seqs[2]["stops"][0]["lon"] = seqs[1]["stops"][2]["lon"]
    seqs[3]["stops"][0]["lon"] = seqs[2]["stops"][2]["lon"]
    origins = [(0, 0, 0)]
    destinations = [(3, 2, 2)]
    journeys = build_journeys(
        origins,
        destinations,
        seqs,
        stop_time_min=2.0,
        wait_time_min=0.0,
        walk_to_stop_min=0.0,
        transfer_penalty_min=5.0,
        transfer_wait_min=0.0,
        transfer_radius_m=800.0,
        max_transfers=3,
        transfer_penalty_calc="fixed",
        seq_headway_min=None,
        seq_jitter_s=None,
        wait_calc="takt",
    )
    assert any(len(legs) == 4 for _time, legs in journeys)
    assert len(journeys) <= 3



def test_route_sequence_preserves_per_period_headways() -> None:
    from types import SimpleNamespace
    stops = [
        SimpleNamespace(id=1, name="A", latitude=52.0, longitude=4.0),
        SimpleNamespace(id=2, name="B", latitude=52.01, longitude=4.01),
    ]
    direction = SimpleNamespace(name="D", stops=stops)
    route = SimpleNamespace(
        ok=True, route_id=510, name="bus 510", route_type="bus",
        directions=[direction], headways=[5.0, 10.0, 20.0, 30.0, 40.0],
    )
    seq = _build_route_stop_sequence([route])[0]
    assert seq["headways"] == (5.0, 10.0, 20.0, 30.0, 40.0)


def test_period_fleet_uses_maximum_period_fleet() -> None:
    seq = _synthetic_sequence([1, 2, 3])
    seq["headways"] = (5.0, 10.0, 20.0, 30.0, 40.0)
    metrics = _period_service_metrics(
        seq, __import__("passenger_flow.base.models", fromlist=["VehicleSpec"]).VEHICLE_DEFAULTS["bus"],
        tuple(({}, h) for h in (2.0, 3.0, 6.0, 4.0, 5.0)),
        10.0, None,
    )
    assert metrics[0] == 3.0
    expected_runs = 2.0 * (2*60/5 + 3*60/10 + 6*60/20 + 4*60/30 + 5*60/40)
    one_way_km = sum(
        haversine_meters(
            seq["stops"][i]["lat"], seq["stops"][i]["lon"],
            seq["stops"][i+1]["lat"], seq["stops"][i+1]["lon"],
        ) / 1000.0 for i in range(2)
    )
    assert math.isclose(metrics[1], expected_runs * one_way_km, rel_tol=1e-12, abs_tol=1e-12)
def test_route_sequence_preserves_row_specific_takt_speed() -> None:
    from types import SimpleNamespace

    stops = [
        SimpleNamespace(id=1, name="A", latitude=52.0, longitude=4.0),
        SimpleNamespace(id=2, name="B", latitude=52.01, longitude=4.01),
    ]
    direction = SimpleNamespace(name="D", stops=stops)
    route = SimpleNamespace(
        ok=True,
        route_id=501,
        name="tram 501",
        route_type="tram",
        directions=[direction],
        row="reserved",
        closed=False,
        bothWays=False,
    )
    seq = _build_route_stop_sequence([route])[0]
    assert seq["row"] == "reserved"
    assert math.isclose(seq["speed_kmh"], 25.0, rel_tol=1e-12, abs_tol=1e-12)
def test_route_sequence_uses_per_segment_row_speed_without_cumt() -> None:
    from types import SimpleNamespace

    stops = [
        SimpleNamespace(id=1, name="A", latitude=52.0, longitude=4.0),
        SimpleNamespace(id=2, name="B", latitude=52.0, longitude=4.01),
        SimpleNamespace(id=3, name="C", latitude=52.0, longitude=4.02),
    ]
    direction = SimpleNamespace(name="D", stops=stops)
    route = SimpleNamespace(
        ok=True,
        route_id=502,
        name="tram mixed rows",
        route_type="tram",
        directions=[direction],
        rows=["reserved", "mixed"],
        closed=False,
        bothWays=False,
    )
    seq = _build_route_stop_sequence([route])[0]
    first = seq["cum_t_s"][1] - seq["cum_t_s"][0]
    second = seq["cum_t_s"][2] - seq["cum_t_s"][1]
    assert first < second



def test_routing_allows_return_to_a_previously_used_line() -> None:
    a = _synthetic_sequence([1, 2, 3, 4])
    b = _synthetic_sequence([2, 5, 3])
    a["route_id"] = 701
    b["route_id"] = 702
    a["cum_t_s"] = [0.0, 60.0, 120.0, 180.0]
    a["segment_time_s"] = (60.0, 60.0, 60.0)
    a["open_pre"] = [0, 1, 2, 3, 4]
    b["cum_t_s"] = [0.0, 60.0, 120.0]
    b["segment_time_s"] = (60.0, 60.0)
    b["open_pre"] = [0, 1, 2, 3]
    journeys = build_journeys(
        [(0, 0, 0)],
        [(0, 3, 3)],
        [a, b],
        stop_time_min=2.0,
        wait_time_min=0.0,
        walk_to_stop_min=0.0,
        transfer_penalty_min=0.0,
        transfer_wait_min=0.0,
        transfer_radius_m=800.0,
        max_transfers=2,
        transfer_penalty_calc="fixed",
        seq_headway_min=None,
        seq_jitter_s=None,
        wait_calc="takt",
    )
    assert any(sum(1 for seq_idx, _a, _b in journey.legs if seq_idx == 0) >= 2 for journey in journeys)

def test_direct_route_allows_reverse_travel_within_direction_sequence() -> None:
    seq = _synthetic_sequence([1, 2, 3])
    journeys = build_journeys(
        [(0, 2, 2)],
        [(0, 0, 0)],
        [seq],
        stop_time_min=2.0,
        wait_time_min=0.0,
        walk_to_stop_min=0.0,
        transfer_penalty_min=5.0,
        transfer_wait_min=0.0,
        transfer_radius_m=800.0,
        max_transfers=0,
        transfer_penalty_calc="fixed",
        seq_headway_min={0: 10.0},
        seq_jitter_s={0: 0.0},
        wait_calc="takt",
    )
    assert journeys
    assert journeys[0].legs == ((0, 2, 0),)

def test_routing_edge_includes_directional_crowding_feedback() -> None:
    seq = _synthetic_sequence([1, 2, 3])
    base = _ride_edge_time_min(
        seq, 0, 2, stop_time_min=2.0, crowd_state=None
    )
    crowded = _ride_edge_time_min(
        seq, 0, 2,
        stop_time_min=2.0,
        crowd_state={
            "seg_forward": {(0, 0): 1.5, (0, 1): 1.5},
            "seg_reverse": {(0, 0): 0.5, (0, 1): 0.5},
            "stop_extra": {(0, 1): 10.0},
        },
    )
    assert crowded > base

def test_same_stop_transfer_is_reachable_in_state_graph() -> None:
    seq_a = _synthetic_sequence([1, 2, 3])
    seq_b = _synthetic_sequence([1, 4, 5])
    seq_a["route_id"] = 201
    seq_b["route_id"] = 202
    journeys = build_journeys(
        [(0, 0, 0)],
        [(1, 2, 2)],
        [seq_a, seq_b],
        stop_time_min=2.0,
        wait_time_min=0.0,
        walk_to_stop_min=0.0,
        transfer_penalty_min=0.0,
        transfer_wait_min=0.0,
        transfer_radius_m=800.0,
        max_transfers=1,
        transfer_penalty_calc="fixed",
        seq_headway_min={0: 10.0, 1: 10.0},
        seq_jitter_s={0: 0.0, 1: 0.0},
        wait_calc="takt",
    )
    assert any(
        len(j.legs) == 2 and j.legs[0] == (0, 0, 0) and j.legs[1][0] == 1
        for j in journeys
    )


def test_closed_one_way_route_does_not_add_reverse_state() -> None:
    seq = _synthetic_sequence([1, 2, 3])
    seq["closed"] = True
    seq["both_ways"] = False
    journeys = build_journeys(
        [(0, 1, 1)],
        [(0, 0, 0)],
        [seq],
        stop_time_min=2.0,
        wait_time_min=0.0,
        walk_to_stop_min=0.0,
        transfer_penalty_min=0.0,
        transfer_wait_min=0.0,
        transfer_radius_m=800.0,
        max_transfers=0,
        transfer_penalty_calc="fixed",
        seq_headway_min={0: 10.0},
        seq_jitter_s={0: 0.0},
        wait_calc="takt",
    )
    assert journeys
    assert journeys[0].legs == ((0, 1, 0),)


def test_transfer_index_is_specific_to_current_stop() -> None:
    a = _synthetic_sequence([1, 2, 3])
    b = _synthetic_sequence([4, 5, 6])
    a["_seq_idx"] = 0; b["_seq_idx"] = 1
    b["stops"][0]["lat"] = a["stops"][0]["lat"] + 0.0001
    b["stops"][1]["lat"] = a["stops"][2]["lat"] + 0.0001
    index = _build_transfer_edge_index([a, b], 800.0)
    first_targets = index[(0, 0)]
    last_targets = index[(0, 2)]
    assert first_targets and last_targets
    assert first_targets[0][2]["id"] == 4
    assert last_targets[0][2]["id"] == 6

def test_transfer_graph_keeps_nearest_stop_per_target_line() -> None:
    seq_a = _synthetic_sequence([1, 2, 3])
    seq_b = _synthetic_sequence([4, 5, 6])
    seq_b["stops"][0]["lat"] = seq_a["stops"][1]["lat"] + 0.0001
    seq_b["stops"][1]["lat"] = seq_a["stops"][1]["lat"] + 0.0002
    seq_c = _synthetic_sequence([7, 8, 9])
    seq_c["stops"][0]["lat"] = seq_a["stops"][1]["lat"] + 0.0003
    seq_c["stops"][1]["lat"] = seq_a["stops"][1]["lat"] + 0.0004
    from passenger_flow.network.routes import _transfer_targets

    got = list(_transfer_targets(0, 0, [seq_a, seq_b, seq_c], set((0,)), 800.0))
    by_origin = {}
    for line, ta, tb in got:
        by_origin.setdefault((ta["position"], line), []).append(tb["id"])
    for key, ids in by_origin.items():
        assert len(ids) == 1, key
    assert set(line for _ta_pos, line in by_origin) == {1, 2}
    assert 5 in [ids[0] for (pos, line), ids in by_origin.items() if line == 1 and pos == 1]


def test_journey_crowding_uses_selected_directional_load() -> None:
    seq = _synthetic_sequence([1, 2, 3])
    seq["speed_kmh"] = 18.0
    state = {
        "seg_forward": {(0, 0): 0.5, (0, 1): 0.5},
        "seg_reverse": {(0, 0): 2.0, (0, 1): 2.0},
        "stop_extra": {},
    }
    forward = JourneyAlternative(10.0, ((0, 0, 2),))
    reverse = JourneyAlternative(10.0, ((0, 2, 0),))
    got = _journey_crowd_extra(
        [forward, reverse],
        [seq],
        state,
        {0: 10.0},
    )
    assert got[0] < got[1]

def test_line_opex_includes_daily_vehicle_cost() -> None:
    seq = _synthetic_sequence([1, 2, 3])
    seq["route_id"] = 7
    seq["route_name"] = "bus 7"
    results = _build_line_kpis(
        [seq],
        {7: 100.0},
        10.0,
        0.0,
        100.0,
        None,
    )
    result = results[0]
    assert result.fleet >= 1.0
    assert result.opex_day > result.fleet * 250.0

def test_closed_route_uses_full_cycle_endpoint_cumt() -> None:
    seq = _synthetic_sequence([1, 2, 3])
    seq["closed"] = True
    seq["cycle_run_s"] = 180.0
    assert math.isclose(
        _route_ride_time_min(seq, 2, 0),
        1.0,
        rel_tol=1e-12,
        abs_tol=1e-12,
    )

def test_base_time_nan_is_treated_as_missing() -> None:
    _validate_base_time(
        np.asarray([[np.nan, 900.0], [1200.0, 0.0]], dtype=np.float64),
        2,
        1,
    )


def test_explicit_takt_row_drives_segment_capital_cost() -> None:
    seq = _synthetic_sequence([1, 2, 3])
    seq["route_type_key"] = "tram"
    seq["row"] = "reserved"
    seq["row_explicit"] = True
    seq["seg_cost_mul"] = [1.0, 1.0]
    spec = __import__("passenger_flow.base.models", fromlist=["VehicleSpec"]).VEHICLE_DEFAULTS["tram"]
    distance_km = sum(
        haversine_meters(
            seq["stops"][i]["lat"], seq["stops"][i]["lon"],
            seq["stops"][i + 1]["lat"], seq["stops"][i + 1]["lon"],
        ) / 1000.0
        for i in range(2)
    )
    got = _sequence_capital_cost_eur(seq, spec, 1.0)
    assert math.isclose(got, distance_km * 18_000_000.0, rel_tol=1e-12, abs_tol=1e-8)

def test_ontrack_resolves_source_track_geometry() -> None:
    source = _synthetic_sequence([1, 2])
    source["route_id"] = 1001
    source["track_id"] = "T1001"
    source["stops"][0]["lon"], source["stops"][0]["lat"] = 4.00000, 52.00000
    source["stops"][1]["lon"], source["stops"][1]["lat"] = 4.02000, 52.00000
    source["geometry_legs"] = [[
        [4.00000, 52.00000], [4.01000, 52.00000], [4.02000, 52.00000]
    ]]
    dependent = _synthetic_sequence([3, 4])
    dependent["route_id"] = 1002
    dependent["stops"][0]["lon"], dependent["stops"][0]["lat"] = 4.00000, 52.00000
    dependent["stops"][1]["lon"], dependent["stops"][1]["lat"] = 4.02000, 52.00000
    dependent["on_track"] = ["T1001"]
    by_track = {"T1001": source}
    edges = _geometry_segment_edges(dependent, 0, by_track, {"1002"})
    assert len(edges) == 2
    assert edges[0][0] == [4.00000, 52.00000]
    assert edges[-1][1] == [4.02000, 52.00000]

def test_capex_reuses_partial_decoded_polyline_geometry() -> None:
    a = _synthetic_sequence([1, 2])
    b = _synthetic_sequence([3, 4])
    for seq in (a, b):
        seq["route_type_key"] = "tram"
        seq["route_type"] = "tram"
        seq["row"] = "reserved"
        seq["row_explicit"] = True
    a["stops"][0]["lon"], a["stops"][0]["lat"] = 4.00000, 52.00000
    a["stops"][1]["lon"], a["stops"][1]["lat"] = 4.02000, 52.00000
    b["stops"][0]["lon"], b["stops"][0]["lat"] = 4.01000, 52.00000
    b["stops"][1]["lon"], b["stops"][1]["lat"] = 4.02000, 52.00000
    a["geometry_legs"] = [[
        [4.00000, 52.00000], [4.01000, 52.00000], [4.02000, 52.00000]
    ]]
    b["geometry_legs"] = [[
        [4.01000, 52.00000], [4.02000, 52.00000]
    ]]
    spec = __import__("passenger_flow.base.models", fromlist=["VehicleSpec"]).VEHICLE_DEFAULTS["tram"]
    reuse = _geometry_reuse_edges([b])
    got = _sequence_capital_cost_eur(a, spec, 1.0, geometry_reuse_edges=reuse)
    total_m = haversine_meters(52.0, 4.0, 52.0, 4.01) + haversine_meters(52.0, 4.01, 52.0, 4.02)
    remaining_m = haversine_meters(52.0, 4.0, 52.0, 4.01)
    expected = remaining_m / 1000.0 * 18_000_000.0
    assert math.isclose(total_m, haversine_meters(52.0, 4.0, 52.0, 4.02), rel_tol=1e-6)
    assert math.isclose(got, expected, rel_tol=1e-9, abs_tol=1e-6)

def test_shared_physical_section_is_not_charged_twice() -> None:
    seq_a = _synthetic_sequence([1, 2, 3])
    seq_b = _synthetic_sequence([1, 2, 3])
    seq_a["route_id"] = 801
    seq_b["route_id"] = 802
    for seq in (seq_a, seq_b):
        seq["route_type_key"] = "tram"
        seq["row"] = "reserved"
        seq["row_explicit"] = True
        seq["seg_cost_mul"] = [1.0, 1.0]
    results = _build_line_kpis(
        [seq_a, seq_b],
        {801: 100.0, 802: 100.0},
        10.0,
        0.0,
        200.0,
        None,
    )
    assert len(results) == 2
    assert results[0].capital_cost_eur > 0.0
    assert math.isclose(results[1].capital_cost_eur, 0.0, abs_tol=1e-8)

def test_atomic_infrastructure_sections_split_shared_segment_at_intermediate_stop() -> None:
    a = _synthetic_sequence([1, 2])
    b = _synthetic_sequence([3, 4, 5])
    a["_seq_idx"] = 0; a["route_id"] = 901
    b["_seq_idx"] = 1; b["route_id"] = 902
    a["route_type_key"] = b["route_type_key"] = "tram"
    coords_a = [(52.0, 4.0), (52.0, 4.02)]
    coords_b = [(52.0, 4.0), (52.0, 4.01), (52.0, 4.02)]
    for seq, coords in ((a, coords_a), (b, coords_b)):
        for stop, (lat, lon) in zip(seq["stops"], coords):
            stop["lat"], stop["lon"] = lat, lon
    lines, pieces = _atomic_infrastructure_sections([a, b])
    shared = [key for key, owners in lines.items() if owners == {901, 902}]
    assert len(shared) == 2
    assert len(pieces[(0, 0)]) == 2


def test_shared_capacity_uses_atomic_overlap_with_intermediate_stop() -> None:
    a = _synthetic_sequence([1, 2])
    b = _synthetic_sequence([3, 4, 5])
    a["_seq_idx"] = 0; a["route_id"] = 911
    b["_seq_idx"] = 1; b["route_id"] = 912
    for seq in (a, b):
        seq["route_type_key"] = "tram"
        seq["route_type"] = "tram"
    for stop, (lat, lon) in zip(a["stops"], [(52.0, 4.0), (52.0, 4.02)]):
        stop["lat"], stop["lon"] = lat, lon
    for stop, (lat, lon) in zip(b["stops"], [(52.0, 4.0), (52.0, 4.01), (52.0, 4.02)]):
        stop["lat"], stop["lon"] = lat, lon
    got = _shared_capacity_min_headways([a, b], 10.0, {911: 2.0, 912: 6.0})
    assert math.isclose(got[911], 2.0, rel_tol=1e-9, abs_tol=1e-9)
    assert math.isclose(got[912], 6.0, rel_tol=1e-9, abs_tol=1e-9)

def test_takt_co_branches_only_the_transfer_leg() -> None:
    a = _synthetic_sequence([1, 2, 3])
    b = _synthetic_sequence([2, 4, 5])
    c = _synthetic_sequence([2, 6, 5])
    for idx, seq in enumerate((a, b, c)):
        seq["_seq_idx"] = idx
    a["route_id"] = 801; b["route_id"] = 802; c["route_id"] = 803
    journeys = [JourneyAlternative(20.0, ((0, 0, 1), (1, 0, 2)))]
    transfer_index = {
        (0, 1): (
            (1, b["stops"][0], b["stops"][0]),
            (2, c["stops"][0], c["stops"][0]),
        )
    }
    state = {
        "seg_forward": {(1, 0): 1.0, (2, 0): 1.0},
        "seg_reverse": {}, "stop_extra": {}, "unreliability": {},
    }
    base_candidates, base_probs = _takt_leg_choice_probs(
        journeys[0], 0, [a, b, c],
        period_index=0, seq_headway_min={0: 10.0, 1: 10.0, 2: 20.0},
        seq_jitter_s={0: 0.0, 1: 0.0, 2: 0.0}, crowd_state=state,
        transfer_index=transfer_index, stop_time_min=0.0, transfer_radius_m=800.0,
    )
    leg_candidates, leg_probs = _takt_leg_choice_probs(
        journeys[0], 1, [a, b, c],
        period_index=0, seq_headway_min={0: 10.0, 1: 10.0, 2: 20.0},
        seq_jitter_s={0: 0.0, 1: 0.0, 2: 0.0}, crowd_state=state,
        transfer_index=transfer_index, stop_time_min=0.0, transfer_radius_m=800.0,
    )
    assert base_candidates == ((0, 0, 1),)
    assert np.allclose(base_probs, [1.0])
    assert len(leg_candidates) >= 1
    assert math.isclose(float(leg_probs.sum()), 1.0, rel_tol=1e-12, abs_tol=1e-12)
    assert any(leg[0] == 2 for leg in leg_candidates[1:])

def test_transfer_crowding_uses_hs_times_load_factor() -> None:
    a = _synthetic_sequence([1, 2, 3])
    b = _synthetic_sequence([1, 4, 5])
    a["route_id"] = 601
    b["route_id"] = 602
    journeys = [JourneyAlternative(
        10.0, ((0, 0, 1), (1, 0, 2))
    )]
    state = {
        "seg_forward": {(0, 0): 1.0, (1, 1): 1.5},
        "seg_reverse": {},
        "stop_extra": {},
        "unreliability": {},
    }
    got = _journey_crowd_extra(
        journeys, [a, b], state,
        {0: 10.0, 1: 10.0},
        period_index=0,
        seq_jitter_s={0: 90.0, 1: 60.0},
    )
    from passenger_flow.network.routes import _scheduled_transfer_wait_min
    expected_wait = _scheduled_transfer_wait_min(
        0, 1, a["stops"][1], b["stops"][2],
        stop_time_min=0.0,
        route_stop_sequences=[a, b],
        seq_headway_min={0: 10.0, 1: 10.0},
        seq_jitter_s={0: 90.0, 1: 60.0},
    )
    assert math.isclose(got[0], expected_wait * 0.5, rel_tol=1e-12, abs_tol=1e-12)

def test_station_qa_can_raise_min_headway_above_vehicle_track_limit() -> None:
    seq = _synthetic_sequence([1, 2, 3])
    seq["route_type_key"] = "bus"
    seq["route_type"] = "bus"
    results = _build_line_kpis(
        [seq],
        {1: 0.0},
        10.0,
        0.0,
        0.0,
        None,
        period_seq_stop_totals=[({}, 2.0)],
    )
    assert len(results) == 1
    assert math.isclose(results[0].min_headway, 0.75, rel_tol=1e-9, abs_tol=1e-9)

def test_station_qa_uses_peak_stop_rate_not_pax_per_train() -> None:
    seq = _synthetic_sequence([1, 2, 3])
    results = _build_line_kpis(
        [seq],
        {1: 100.0},
        10.0,
        0.0,
        100.0,
        None,
        period_seq_stop_totals=[({(0, 0): 120.0}, 2.0)],
    )
    expected = (20.0 + 25.0) / (60.0 - 2.0 * (120.0 / 2.0) / 60.0)
    assert math.isclose(results[0].min_headway, expected, rel_tol=1e-12, abs_tol=1e-12)

def test_fixed_build_ranges_charge_full_segment_before_reuse() -> None:
    seq_a = _synthetic_sequence([1, 2, 3])
    seq_b = _synthetic_sequence([1, 2, 3])
    seq_a["route_type_key"] = seq_b["route_type_key"] = "tram"
    seq_a["row"] = seq_b["row"] = "reserved"
    seq_a["row_explicit"] = seq_b["row_explicit"] = True
    seq_a["fixed_legs"] = [{"buildRanges": [[0, 1]]}, None]
    spec = __import__("passenger_flow.base.models", fromlist=["VehicleSpec"]).VEHICLE_DEFAULTS["tram"]
    shared: set[tuple[str, str, str]] = set()
    first = _sequence_capital_cost_eur(seq_a, spec, 1.0, shared)
    second = _sequence_capital_cost_eur(seq_b, spec, 1.0, shared)
    assert first > 0.0
    assert second < first

def test_tram_capital_cost_is_separate_from_daily_amortization() -> None:
    seq = _synthetic_sequence([1, 2, 3])
    seq["route_id"] = 8
    seq["route_name"] = "tram 8"
    seq["route_type"] = "tram"
    seq["route_type_key"] = "tram"
    results = _build_line_kpis(
        [seq],
        {8: 100.0},
        10.0,
        0.0,
        100.0,
        None,
    )
    result = results[0]
    assert result.capital_cost_eur > 0.0
    assert math.isclose(
        result.capex_day,
        result.capital_cost_eur / (365.0 * 30.0),
        rel_tol=1e-12,
        abs_tol=1e-12,
    )


def test_journey_alternative_exposes_named_fields() -> None:
    option = JourneyAlternative(12.5, ((0, 1, 2),))
    assert option.total_time_min == 12.5
    assert option.legs == ((0, 1, 2),)
    assert option[0] == option.total_time_min


def test_purpose_od_writer_preserves_base_time(tmp_path) -> None:
    from od.demand.takt import load_takt_purposes, write_takt_purposes
    from od.model import PurposeOd
    from passenger_flow.models import Purpose
    from scipy import sparse

    purpose = Purpose(
        key="edu", label="Education", trips_per_res=0.1, d0_m=1000.0,
        out=(1.0,), ret=(1.0,),
    )
    mat = sparse.csr_matrix(
        np.asarray([[0.0, 7.0], [3.0, 0.0]], dtype=np.float64)
    )
    base = np.asarray([[900.0, 1200.0], [930.0, 1260.0]], dtype=np.float64)
    source = PurposeOd(
        matrix=mat.toarray(),
        purpose_matrices=(mat,),
        period_out=(1.0,),
        period_ret=(1.0,),
        shares=(1.0,),
        purposes=(purpose,),
        purpose_base_times=(base,),
        commute_base_time=np.asarray([[600.0, 800.0], [630.0, 840.0]], dtype=np.float64),
    )
    path = tmp_path / "purpose-od.json"
    costs = np.zeros((2, 2), dtype=np.float64)
    write_takt_purposes(source, costs, path)
    loaded = load_takt_purposes(path)
    assert np.array_equal(loaded.layers[0].base_time, base.astype("<f4"))
    assert np.array_equal(
        loaded.commute_base_time, source.commute_base_time.astype("<f4")
    )


def test_takt_purposes_base_time_roundtrip(tmp_path) -> None:
    from od.demand.takt import load_takt_purposes, write_takt_purposes_bundle
    from od.model import TaktPurposeLayer, TaktPurposes

    pairs = np.asarray(
        [[0.0, 1.0, 7.0, 900.0], [1.0, 0.0, 3.0, 1200.0]],
        dtype="<f4",
    )
    base_time = np.asarray(
        [[900.0, 1200.0], [930.0, 1260.0]],
        dtype="<f4",
    )
    commute = np.asarray([[600.0, 800.0], [630.0, 840.0]], dtype="<f4")
    source = TaktPurposes(
        layers=(
            TaktPurposeLayer(
                key="edu",
                out=(0.1, 0.9),
                ret=(0.8, 0.2),
                pairs=pairs,
                base_time=base_time,
            ),
        ),
        commute_base_time=commute,
    )
    path = tmp_path / "purposes.bin.json"
    write_takt_purposes_bundle(source, path)
    loaded = load_takt_purposes(path)

    assert loaded.layers[0].key == source.layers[0].key
    assert np.array_equal(loaded.layers[0].pairs, source.layers[0].pairs)
    assert np.array_equal(loaded.layers[0].base_time, source.layers[0].base_time)
    assert np.array_equal(loaded.commute_base_time, source.commute_base_time)


def test_flow_report_denominator_uses_periodized_directional_demand() -> None:
    from passenger_flow.base.models import PeriodFlow

    base_od_trips = 916_174.0
    from passenger_flow.base.takt import TAKT_PERIODS
    period_flows = tuple(
        PeriodFlow(
            p.key,
            p.label,
            base_od_trips * (p.out + p.ret),
            0.0, 0.0, 0.0, 0.0, 0.0,
        )
        for p in TAKT_PERIODS
    )

    assert math.isclose(
        sum(p.total_trips for p in period_flows),
        2.0 * base_od_trips,
        rel_tol=0.0,
        abs_tol=1e-9,
    )


def test_prepared_flow_api_accepts_reusable_static_context(monkeypatch) -> None:
    import passenger_flow.core as core

    from od.model import Zones

    zones = Zones(
        xy=np.asarray([[4.0, 52.0], [4.002, 52.002]], dtype=float),
        ids=np.asarray([1, 2], dtype=np.int64),
        polygons=(None, None),
        bounds=(4.0, 52.0, 4.002, 52.002),
    )

    monkeypatch.setattr(core, "_build_route_stop_sequence", lambda _routes: [_synthetic_sequence([1, 2, 3])])
    prepared = core.prepare_passenger_flow([], zones, stop_search_radius_m=1500.0)
    assert prepared.zones is zones
    assert len(prepared.route_sequences) == 1
    assert 0 in prepared.zone_nearest and 1 in prepared.zone_nearest


def test_run_passenger_flow_reuses_prepared_context() -> None:
    from od.model import Zones
    from passenger_flow.core import PreparedPassengerFlow, run_passenger_flow

    zones = Zones(
        xy=np.asarray([[4.0, 52.0], [4.002, 52.002]], dtype=float),
        ids=np.asarray([1, 2], dtype=np.int64),
        polygons=(None, None),
        bounds=(4.0, 52.0, 4.002, 52.002),
    )
    prepared = PreparedPassengerFlow(
        zones=zones,
        stop_search_radius_m=1500.0,
        route_sequences=(),
        zone_nearest={0: [], 1: []},
    )
    result = run_passenger_flow(
        [],
        np.zeros((2, 2), dtype=np.float64),
        zones,
        prepared=prepared,
    )
    assert result.routes_served == 0
    assert result.total_trips == 0.0


def test_sparse_od_matrix_is_supported():
    import scipy.sparse as sp
    from od.model import Zones
    from passenger_flow import run_passenger_flow

    zones = Zones(
        ids=np.array([1, 2], dtype=np.int64),
        polygons=(None, None),
        xy=np.array([[0.0, 52.37], [0.005, 52.37]], dtype=np.float64),
        bounds=(-0.001, 52.369, 0.006, 52.371),
    )
    od = sp.csr_matrix(
        (np.array([10.0, 10.0]), (np.array([0, 1]), np.array([1, 0]))),
        shape=(2, 2),
    )
    result = run_passenger_flow([], od, zones, od_sparse=od, periods=())
    assert result.total_trips == 20.0


def test_flow_result_exposes_takt_diagnostics():
    from passenger_flow.base.models import FlowResult
    result = FlowResult(route_flows=(), stop_flows=(), total_trips=0.0, assigned_trips=0.0, routes_served=0)
    assert result.takt_diagnostics == {}


def test_p6_city_release_manifest_is_pinned() -> None:
    from scripts.takt_release_gate import validate_city_manifest

    manifest = json.loads(
        (Path(__file__).parent / "fixtures" / "takt_release_city_cases.json").read_text(
            encoding="utf-8"
        )
    )
    names = validate_city_manifest(manifest, _repo_root())
    assert names == ["amsterdam-v8", "berlin-v5", "hong-kong-v6"]


def test_p6_release_gate_rejects_changed_bundle(tmp_path: Path) -> None:
    from scripts.takt_release_gate import ReleaseGateError, verify_bundle_provenance

    reference = json.loads(
        (Path(__file__).parent / "fixtures" / "takt_reference_snapshot.json").read_text(
            encoding="utf-8"
        )
    )
    bundle = tmp_path / "bundle.js"
    bundle.write_bytes(b"changed")
    reference["reference"]["source"] = "bundle.js"
    with pytest.raises(ReleaseGateError):
        verify_bundle_provenance(reference, tmp_path)
