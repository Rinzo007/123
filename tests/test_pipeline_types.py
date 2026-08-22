from wikiroutes.pipeline_types import PipelineContext, PipelineResult


def test_pipeline_context_and_result_are_importable():
    assert PipelineContext.__name__ == "PipelineContext"
    assert PipelineResult.__name__ == "PipelineResult"
