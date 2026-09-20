"""合计行打标：行首词表 + 校验和（难点13/N6）。"""
from tests._excel_fixtures import build_xlsx


def _grid(rows):
    from document2chunk.extractors.excel.reader import read_sheet_grids

    grids, _ = read_sheet_grids(build_xlsx({"s": rows}))
    return grids[0]


def _region(grid):
    from document2chunk.extractors.excel.boundary import split_regions

    return split_regions(grid)[0]


def test_red_keyword_total_row():
    from document2chunk.extractors.excel.totals import mark_total_rows

    g = _grid([["科目", "金额"], ["人员费", 100], ["设备费", 200], ["合计", 300]])
    marks = mark_total_rows(g, _region(g), 1)
    assert marks == {3: "keyword"}


def test_red_checksum_total_row_without_label():
    from document2chunk.extractors.excel.totals import mark_total_rows

    g = _grid([["科目", "金额"], ["人员费", 100], ["设备费", 200], [None, 300]])
    marks = mark_total_rows(g, _region(g), 1)
    assert marks == {3: "checksum"}


def test_red_normal_row_not_marked():
    from document2chunk.extractors.excel.totals import mark_total_rows

    g = _grid([["科目", "金额"], ["人员费", 100], ["设备费", 200], ["材料费", 50]])
    assert mark_total_rows(g, _region(g), 1) == {}


def test_red_column_named_total_not_misleading():
    from document2chunk.extractors.excel.totals import mark_total_rows

    # 词表只匹配行首列：行首是科目名、首列表头恰含"合计金额"的数据行不得误杀
    g = _grid([["科目", "合计金额"], ["人员费", 100], ["设备费", 200]])
    assert mark_total_rows(g, _region(g), 1) == {}
