"""表边界：空行空列切分。"""
from tests._excel_fixtures import build_xlsx

_H = [["名称", "数量"]]


def _grid(data_rows, name="s"):
    from document2chunk.extractors.excel.reader import read_sheet_grids

    grids, _ = read_sheet_grids(build_xlsx({name: [_H[0]] + data_rows}))
    return grids[0]


def test_red_single_table_one_region():
    from document2chunk.extractors.excel.boundary import split_regions

    g = _grid([["甲", 1], ["乙", 2]])
    regions = split_regions(g)
    assert len(regions) == 1
    assert (regions[0].r1, regions[0].c1, regions[0].r2, regions[0].c2) == (0, 0, 2, 1)


def test_red_stacked_tables_split_by_blank_row():
    from document2chunk.extractors.excel.boundary import split_regions

    # 网格 = 表头行 + 3 数据行：r0 名称 / r1 甲 / r2 空 / r3 乙
    g = _grid([["甲", 1], [None, None], ["乙", 2]])
    regions = split_regions(g)
    assert len(regions) == 2
    assert regions[0].r1 == 0 and regions[0].r2 == 1
    assert regions[1].r1 == 3 and regions[1].r2 == 3


def test_red_side_by_side_tables_split_by_blank_col():
    from document2chunk.extractors.excel.boundary import split_regions
    from document2chunk.extractors.excel.reader import read_sheet_grids

    grids, _ = read_sheet_grids(
        build_xlsx({"s": [["名称", "数量", None, "姓名"], ["甲", 1, None, "张三"]]})
    )
    regions = split_regions(grids[0])
    assert len(regions) == 2
    assert regions[0].c2 == 1
    assert regions[1].c1 == 3


def test_red_empty_grid_no_regions():
    from document2chunk.extractors.excel.boundary import split_regions
    from document2chunk.extractors.excel.models import SheetGrid

    assert split_regions(SheetGrid(name="empty")) == []


def test_red_whitespace_only_cell_is_blank():
    from document2chunk.extractors.excel.boundary import split_regions

    g = _grid([["甲", 1], ["  ", None], ["乙", 2]])
    assert len(split_regions(g)) == 2
