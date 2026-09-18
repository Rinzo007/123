def test_public_api_is_lazy():
    import overture

    assert "compute_overture_result" in overture.__all__
    from overture import OvertureConfig

    assert OvertureConfig.cache_version == 12


def test_legacy_poi_loader_import_is_preserved():
    from overture.load import resolve_poi_place_file

    assert callable(resolve_poi_place_file)
