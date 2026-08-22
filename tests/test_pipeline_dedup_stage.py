from types import SimpleNamespace

from wikiroutes.pipeline_dedup_stage import run_dedup_pass


def test_dedup_stage_handles_too_small_network() -> None:
    config = SimpleNamespace(ghs=False, dedup_center_weight=0.0, dedup_threshold=0.8)
    routes = [object()]
    analysis = {"ids": [], "meta": {}}

    result = run_dedup_pass(routes, analysis, config)

    assert result["removed"] == []
    assert result["routes"] == routes
    assert result["unit_count"] == 0
