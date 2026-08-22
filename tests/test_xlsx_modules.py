from wikiroutes import xlsx_dedup, xlsx_poi


def test_poi_writer_is_extracted() -> None:
    assert callable(xlsx_poi.write_poi_sheets)


def test_dedup_writer_is_extracted() -> None:
    assert callable(xlsx_dedup.write_dedup_sheet)
