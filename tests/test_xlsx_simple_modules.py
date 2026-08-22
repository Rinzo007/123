from wikiroutes import xlsx_simple


def test_simple_worksheet_writers_are_extracted() -> None:
    names = {
        "write_errors_sheet",
        "write_excluded_sheet",
        "write_unique_stops_sheet",
        "write_stop_volumes_sheet",
        "write_generated_sheet",
        "write_heatmap_sheet",
        "write_all_stops_sheet",
    }
    assert names <= set(dir(xlsx_simple))
