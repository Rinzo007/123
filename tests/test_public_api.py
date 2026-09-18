def test_public_api_is_lazy():
    import overture

    assert "compute_overture_result" in overture.__all__
    from overture import OvertureConfig

    assert OvertureConfig.cache_version == 12
