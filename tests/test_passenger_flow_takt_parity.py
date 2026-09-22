

def test_transfer_index_shares_downstream_suffix_cache() -> None:
    a = _synthetic_sequence([1, 2, 3, 4])
    b = _synthetic_sequence([5, 6, 7, 8])
    a["_seq_idx"] = 0
    b["_seq_idx"] = 1
    b["stops"][0]["lat"] = a["stops"][1]["lat"] + 0.0001
    b["stops"][1]["lat"] = a["stops"][3]["lat"] + 0.0001
    index = _build_transfer_edge_index([a, b], 800.0)

    first = index.downstream_targets([a, b], 0, 0)    second = index.downstream_targets([a, b], 0, 0)

    assert first == second
    assert len(index.downstream_cache) == 1
    assert index.downstream_cache[(0, 0)] is first


def test_transfer_index_precomputes_line_target_candidates() -> None:
    a = _synthetic_sequence([1, 2, 3, 4])
    b = _synthetic_sequence([5, 6, 7, 8])
    c = _synthetic_sequence([9, 10, 11, 12])
    for pos, stop in enumerate(a["stops"]):
        stop["lat"] = 52.0 + pos * 0.01
        stop["lon"] = 4.0 + pos * 0.01
    for pos, stop in enumerate(b["stops"]):
        stop["lat"] = 52.02 + pos * 0.02
        stop["lon"] = 4.02 + pos * 0.02
    for pos, stop in enumerate(c["stops"]):
        stop["lat"] = 53.0 + pos * 0.02
        stop["lon"] = 5.0 + pos * 0.02
    b["stops"][0]["lat"] = a["stops"][1]["lat"] + 0.0001
    b["stops"][0]["lon"] = a["stops"][1]["lon"] + 0.0001
    c["stops"][1]["lat"] = a["stops"][2]["lat"] + 0.0001
    c["stops"][1]["lon"] = a["stops"][2]["lon"] + 0.0001
    index = _build_transfer_edge_index([a, b, c], 800.0)

    got = index.targets_to_line(0, 1)
    assert [pos for pos, _stop in got] == [1]
    assert got[0][1]["id"] == b["stops"][0]["id"]
    assert index.targets_to_line(0, 2) == ((2, c["stops"][1]),)


def test_transfer_index_is_specific_to_current_stop() -> None:
    a = _synthetic_sequence([1, 2, 3])
    b = _synthetic_sequence([4, 5, 6])
    a["_seq_idx"] = 0; b["_seq_idx"] = 1
    b["stops"][0]["lat"] = a["stops"][0]["lat"] + 0.0001
    b["stops"][1]["lat"] = a["stops"][2]["lat"] + 0.0001
    index = _build_transfer_edge_index([a, b], 800.0)
    first_targets = index[(0, 0)]