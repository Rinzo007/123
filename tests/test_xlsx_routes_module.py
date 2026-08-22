from wikiroutes import xlsx as public_xlsx
from wikiroutes import xlsx_runtime
from wikiroutes.xlsx_routes import write_routes_sheet


def test_public_xlsx_wires_extracted_routes_sheet() -> None:
    assert xlsx_runtime._write_routes_sheet is write_routes_sheet
    assert public_xlsx.build_xlsx is xlsx_runtime.build_xlsx
