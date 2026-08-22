from wikiroutes import pipeline_enrichment


def test_enrichment_exports_are_distinct_stage_functions() -> None:
    assert pipeline_enrichment.compute_ghs.__module__ == "wikiroutes.pipeline_enrichment"
    assert pipeline_enrichment.compute_ghs_s.__module__ == "wikiroutes.pipeline_enrichment"
    assert pipeline_enrichment.compute_overture.__module__ == "wikiroutes.pipeline_enrichment"
    assert pipeline_enrichment.compute_poi.__module__ == "wikiroutes.pipeline_enrichment"
