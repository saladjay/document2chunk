"""表头检测与路线分流。"""
from tests._excel_fixtures import build_xlsx


def _grid(rows, merges=()):
    from document2chunk.extractors.excel.reader import read_sheet_grids

    grids, _ = read_sheet_grids(build_xlsx({"s": rows}, merges=merges))
    return grids[0]


def _full(grid):
    from document2chunk.extractors.excel.boundary import split_regions

    regions = split_regions(grid)
    assert len(regions) == 1
    return regions[0]


def test_red_single_row_header():
    from document2chunk.extractors.excel.header import classify_region, detect_header_rows
    from document2chunk.extractors.excel.models import SheetRoute

    g = _grid([["名称", "数量"], ["甲", 1], ["乙", 2]])
    region = _full(g)
    h = detect_header_rows(g, region)
    assert h == 1
    assert classify_region(g, region, h) is SheetRoute.DATA_TABLE


def test_red_two_row_header_with_type_contrast():
    from document2chunk.extractors.excel.header import detect_header_rows

    # 双行表头：第二行也是字符串，但数据行有数字（类型对比）→ 延伸到 2
    g = _grid([["华东", None], ["子列1", "子列2"], ["x", 1]])
    region = _full(g)
    assert detect_header_rows(g, region) == 2


def test_red_pure_text_sheet_not_swallowed():
    from document2chunk.extractors.excel.header import classify_region, detect_header_rows
    from document2chunk.extractors.excel.models import SheetRoute

    # FAQ 型：2 列全字符串长文本 → 只认 1 行表头 + 路线=文档型（盘点修正③ + N5）
    long_q = "为什么上传后没有反应？请检查网络后重试，若仍未解决请联系管理员处理。"
    g = _grid([["问题", "答案"], [long_q, "重启服务即可解决，若仍失败请联系值班同学确认配置。"]])
    region = _full(g)
    h = detect_header_rows(g, region)
    assert h == 1
    assert classify_region(g, region, h) is SheetRoute.DOC_SHEET


def test_red_all_string_many_cols_stays_data_table():
    from document2chunk.extractors.excel.header import classify_region, detect_header_rows
    from document2chunk.extractors.excel.models import SheetRoute

    # 通讯录对照（组织页面数据）：>2 列全字符串 → 数据表，不得误判文档型/多行表头
    g = _grid([["姓名", "部门", "职务"], ["张三", "研发中心", "工程师"], ["李四", "综合部", "经理"]])
    region = _full(g)
    h = detect_header_rows(g, region)
    assert h == 1
    assert classify_region(g, region, h) is SheetRoute.DATA_TABLE


def test_red_form_sheet_by_wide_banner_merge():
    from document2chunk.extractors.excel.header import classify_region, detect_header_rows
    from document2chunk.extractors.excel.models import SheetRoute

    # 决算表型：顶部横向大合并抬头 + 表头 + 数据
    g = _grid(
        [
            ["2024年度研发经费决算表", None, None],
            ["科目", "金额", "备注"],
            ["人员费", 100, None],
            ["设备费", 200, None],
        ],
        merges=("A1:C1",),
    )
    region = _full(g)
    h = detect_header_rows(g, region)
    assert classify_region(g, region, h) is SheetRoute.FORM_SHEET


def test_red_header_cap_at_five():
    from document2chunk.extractors.excel.header import detect_header_rows

    rows = [[f"h{r}", "文本"] for r in range(8)]
    rows.append(["x", 1])
    g = _grid(rows)
    assert detect_header_rows(g, _full(g)) == 5
