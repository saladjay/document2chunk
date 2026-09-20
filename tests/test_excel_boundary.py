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


def test_guard_shredded_records_merged_back():
    """112 实测回归（2.1-2022初步评审）：记录=数据行+备注行+全空行，34 片被误切。
    修正规则②：链长 >=3（恰好隔 1 空行 + 列跨度重叠）回并为一张表。"""
    from document2chunk.extractors.excel.boundary import split_regions
    from document2chunk.extractors.excel.reader import read_sheet_grids

    rows = [
        ["序号", "课题", "评分"],
        [1, "甲", 5], [None, None, "备注A"], [],   # 项目1
        [2, "乙", 6], [None, None, "备注B"], [],   # 项目2
        [3, "丙"], [],                              # 项目3（缺评分→行内列碎片）
        [4, "丁", 7], [None, None, "备注C"], [],    # 项目4
    ]
    grids, _ = read_sheet_grids(build_xlsx({"s": rows}))
    regions = split_regions(grids[0])
    assert len(regions) == 1
    assert (regions[0].r1, regions[0].c1, regions[0].r2, regions[0].c2) == (0, 0, 10, 2)


def test_guard_chain_of_two_stays_split():
    """链长 2（两张独立表仅隔 1 空行）保持分离——结构不可区分，保守不合并。"""
    from document2chunk.extractors.excel.boundary import split_regions

    g = _grid([["甲", 1], [None, None], ["乙", 2]])
    assert len(split_regions(g)) == 2
