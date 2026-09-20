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

from passenger_flow.algorithm.assign import _journey_crowd_extra
from passenger_flow.algorithm.kpis import (
    _atomic_infrastructure_sections,
    _build_line_kpis,
    _sequence_capital_cost_eur,
    _shared_capacity_min_headways,
)
from passenger_flow.core import _takt_car_period_multipliers, _validate_base_time
from passenger_flow.algorithm.wait import _build_crowd_state, _takt_msa_gap
from passenger_flow.algorithm.mode_choice import (
    _takt_car_cost_s,
    _takt_car_period_multiplier,
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
from passenger_flow.network.geometry import haversine_meters
from passenger_flow.network.routes import (
    JourneyAlternative,
    _direct_journeys,
    _ride_edge_time_min,
    _route_ride_time_min,
    build_journeys,
    _build_route_stop_sequence,
)


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
    assert math.isclose(got, 8.0 / 31.0, rel_tol=1e-12, abs_tol=1e-12)

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
        "cycle_run_s": 120.0,
        "dwell_s": 0.0,
        "speed_kmh": 18.0,
        "closed": False,
        "both_ways": False,
        "phase_s": 0.0,
        "open": [True, True, True],
        "open_pre": [0, 1, 2, 3],
    }


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
    assert 4 in [ids[0] for (pos, line), ids in by_origin.items() if line == 1 and pos == 1]


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
