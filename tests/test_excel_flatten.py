"""扁平化与合并下放。"""
from tests._excel_fixtures import build_xlsx


def _grid(rows, merges=()):
    from document2chunk.extractors.excel.reader import read_sheet_grids

    grids, _ = read_sheet_grids(build_xlsx({"s": rows}, merges=merges))
    return grids[0]


def test_red_flatten_single_header():
    from document2chunk.extractors.excel.boundary import split_regions
    from document2chunk.extractors.excel.flatten import flatten_header

    g = _grid([["名称", "数量"], ["甲", 1]])
    keys = flatten_header(g, split_regions(g)[0], 1)
    assert keys == {0: "名称", 1: "数量"}


def test_red_flatten_multilevel_with_merge():
    from document2chunk.extractors.excel.boundary import split_regions
    from document2chunk.extractors.excel.flatten import flatten_header

    # 华东 合并 A1:B1（0-based (0,0)-(0,1)），子列1/子列2 在第二行 → "华东-子列1" / "华东-子列2"
    g = _grid(
        [
            ["华东", None, "华南"],
            ["子列1", "子列2", "子列1"],
            ["x", 1, 2],
        ],
        merges=("A1:B1",),
    )
    keys = flatten_header(g, split_regions(g)[0], 2)
    assert keys[0] == "华东-子列1"
    assert keys[1] == "华东-子列2"
    assert keys[2] == "华南-子列1"


def test_red_flatten_duplicate_and_empty_headers():
    from document2chunk.extractors.excel.boundary import split_regions
    from document2chunk.extractors.excel.flatten import flatten_header

    g = _grid([["名称", None, "名称"], ["a", 1, "b"]])
    keys = flatten_header(g, split_regions(g)[0], 1)
    assert keys[0] == "名称"
    assert keys[1] == "列2"
    assert keys[2] == "名称（2）"


def test_red_flatten_header_newline_squeezed():
    from document2chunk.extractors.excel.boundary import split_regions
    from document2chunk.extractors.excel.flatten import flatten_header

    g = _grid([["科目\n名称", "金额（元）\n2024"], ["人员费", 100]])
    keys = flatten_header(g, split_regions(g)[0], 1)
    assert keys[0] == "科目 名称"
    assert keys[1] == "金额（元） 2024"


def test_red_merge_value_forward_filled():
    from document2chunk.extractors.excel.boundary import split_regions
    from document2chunk.extractors.excel.flatten import fill_region_values

    # 「路段」纵向合并：江罗高速 对 3 行设备（难点1 经典场景）
    g = _grid(
        [
            ["路段", "设备"],
            ["江罗高速", None],
            [None, "摄像头"],
            [None, "传感器"],
        ],
        merges=("A2:A4",),
    )
    region = split_regions(g)[0]
    recs = fill_region_values(g, region, 1)
    assert recs[0] == {0: "江罗高速"}
    assert recs[1] == {0: "江罗高速", 1: "摄像头"}
    assert recs[2] == {0: "江罗高速", 1: "传感器"}
