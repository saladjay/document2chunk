"""Excel 轨 A：数据模型与夹具工厂。"""


def test_red_models_importable():
    from document2chunk.extractors.excel.models import (
        CellFmt, ExcelParseResult, Region, RowRecord, SheetBlock, SheetGrid, SheetRoute,
    )

    g = SheetGrid(name="s")
    assert g.n_rows == 0 and g.n_cols == 0
    assert SheetRoute.DATA_TABLE == "data_table"


def test_red_build_xlsx_fixture_roundtrip():
    from python_calamine import load_workbook
    import io

    from tests._excel_fixtures import build_xlsx

    data = build_xlsx({"Sheet1": [["名称", "数量"], ["甲", 3]]})
    wb = load_workbook(io.BytesIO(data))
    rows = wb.get_sheet_by_name("Sheet1").to_python(skip_empty_area=True)
    assert rows == [["名称", "数量"], ["甲", 3]]


def test_red_excel_parse_error_is_document_error():
    from document2chunk.exceptions import Document2ChunkError, ExcelParseError

    assert issubclass(ExcelParseError, Document2ChunkError)
