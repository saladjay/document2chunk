"""读取层：calamine 值域 + openpyxl 格式二遍。"""
from tests._excel_fixtures import build_xlsx


def test_red_reader_basic_grid():
    from document2chunk.extractors.excel.reader import read_sheet_grids

    data = build_xlsx({"表一": [["名称", "数量"], ["甲", 3]]})
    grids, warnings = read_sheet_grids(data)
    assert warnings == []
    assert len(grids) == 1
    g = grids[0]
    assert g.name == "表一"
    assert g.values == [["名称", "数量"], ["甲", 3]]


def test_red_reader_hidden_sheet_skipped():
    from document2chunk.extractors.excel.reader import read_sheet_grids

    data = build_xlsx(
        {"可见": [["a", 1]], "暗账": [["b", 2]]},
        hidden_sheets=("暗账",),
    )
    grids, warnings = read_sheet_grids(data)
    assert [g.name for g in grids] == ["可见"]
    assert any("暗账" in w for w in warnings)


def test_red_reader_merged_and_formats():
    from document2chunk.extractors.excel.reader import read_sheet_grids

    data = build_xlsx(
        {"s": [["部门", None, "值"], ["华东", None, 1.5], [None, None, 2.5]]},
        merges=("A2:A3",),
        bolds={(0, 0): True},
        number_formats={(1, 2): '"¥"#,##0.00'},
    )
    grids, _ = read_sheet_grids(data)
    g = grids[0]
    assert g.merged == [(1, 0, 2, 0)]          # A2:A3 → 0-based (1,0)-(2,0)
    assert g.formats[(0, 0)].bold is True
    assert "¥" in g.formats[(1, 2)].number_format


def test_red_reader_formula_no_cache_detected():
    from document2chunk.extractors.excel.reader import read_sheet_grids

    data = build_xlsx({"s": [["a", 1], ["b", "=SUM(B1:B1)"]]})
    grids, warnings = read_sheet_grids(data)
    g = grids[0]
    assert (1, 1) in g.formula_no_cache        # openpyxl 写的公式无缓存值
    assert any("无缓存" in w for w in warnings)


def test_red_reader_external_link_warning():
    import zipfile, io

    from document2chunk.extractors.excel.reader import read_sheet_grids

    data = build_xlsx({"s": [["a", 1]]})
    # 在 zip 中补一个 externalLinks 条目以触发检测
    src = zipfile.ZipFile(io.BytesIO(data))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as out:
        for item in src.namelist():
            out.writestr(item, src.read(item))
        out.writestr("xl/externalLinks/externalLink1.xml", "<xml/>")
    grids, warnings = read_sheet_grids(buf.getvalue())
    assert any("外部" in w for w in warnings)
