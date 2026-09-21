"""解析编排与 envelope。"""
from tests._excel_fixtures import build_xlsx


def test_red_full_pipeline_mixed_workbook():
    from document2chunk.extractors.excel.parser import parse_excel_bytes
    from document2chunk.extractors.excel.serializer import to_envelope

    data = build_xlsx(
        {
            "明细": [["名称", "数量"], ["甲", 1], ["乙", 2], ["合计", 3]],
            "FAQ": [
                ["问题", "答案"],
                ["为什么上传后没有反应？请检查网络连接是否正常，或稍后重试。", "已处理完成，若仍未生效请联系管理员核实账号权限配置。"],
            ],
        }
    )
    result = parse_excel_bytes(data, "t.xlsx")
    env = to_envelope(result)
    assert env["file"] == "t.xlsx"
    assert [r["data"] for r in env["rows"][:2]] == [
        {"名称": "甲", "数量": 1},
        {"名称": "乙", "数量": 2},
    ]
    assert env["rows"][0]["text"] == "名称: 甲; 数量: 1"
    totals = [r for r in env["rows"] if r["meta"].get("from_total")]
    assert len(totals) == 1 and totals[0]["data"]["名称"] == "合计"
    assert env["blocks"] and env["blocks"][0]["kind"] == "doc"
    routes = {s["sheet"]: s["routes"] for s in env["sheets"]}
    assert routes["明细"] == ["data_table"]
    assert routes["FAQ"] == ["doc_sheet"]


def test_red_empty_sheet_warning_and_summary():
    from document2chunk.extractors.excel.parser import parse_excel_bytes

    result = parse_excel_bytes(build_xlsx({"空表": [[None]]}), "t.xlsx")
    assert result.rows == []
    assert any("空" in w for w in result.warnings)
    assert result.sheets[0]["routes"] == ["empty"]


def test_red_hidden_row_flagged_but_present():
    from document2chunk.extractors.excel.parser import parse_excel_bytes

    result = parse_excel_bytes(
        build_xlsx({"s": [["名称", "数量"], ["甲", 1], ["乙", 2]]}, hidden_rows=(1,))
    )
    rows = {r.data["名称"]: r for r in result.rows}
    assert rows["甲"].meta["hidden"] is True
    assert rows["乙"].meta["hidden"] is False
    assert rows["甲"].row == 2 and rows["乙"].row == 3   # 1-based 真实行号


def test_red_poison_input_raises_excel_parse_error():
    import pytest

    from document2chunk.exceptions import ExcelParseError
    from document2chunk.extractors.excel.parser import parse_excel_bytes

    with pytest.raises(ExcelParseError):
        parse_excel_bytes(b"PK\x03\x04 not really a zip", "bad.xlsx")


def test_p1_total_layered_definite_and_candidate():
    from document2chunk.extractors.excel.parser import parse_excel_bytes
    from tests._excel_fixtures import build_xlsx

    # 小计行(第3行)关键词命中但算术不符且非末行 → candidate；合计行 → definite
    data = build_xlsx(
        {"s": [["名称", "数量"], ["甲", 1], ["小计", 5], ["乙", 2], ["合计", 3]]}
    )
    result = parse_excel_bytes(data, "t.xlsx")
    rows = {r.data["名称"]: r for r in result.rows}
    assert rows["合计"].meta["from_total"] is True
    assert set(rows["合计"].meta["total_evidence"]) >= {"keyword"}
    assert "from_total" not in rows["小计"].meta
    assert rows["小计"].meta["from_total_candidate"] is True
    assert rows["小计"].meta["total_evidence"] == ["keyword"]
    assert any("第 3 行疑似合计行" in w for w in result.warnings)
    assert "from_total" not in rows["甲"].meta and "from_total_candidate" not in rows["甲"].meta


def test_p1_total_strict_only_downgrades_to_candidate():
    # 22 号 R6 场景：无关键词、算术全吻合 → 旧版误标 from_total，新版只准 candidate
    from document2chunk.extractors.excel.parser import parse_excel_bytes
    from tests._excel_fixtures import build_xlsx

    data = build_xlsx({"s": [["名称", "数量"], ["甲", 1], ["乙", 3], ["丙", 4]]})
    result = parse_excel_bytes(data, "t.xlsx")
    rows = {r.data["名称"]: r for r in result.rows}
    assert "from_total" not in rows["丙"].meta          # 旧版这里 True
    assert rows["丙"].meta["from_total_candidate"] is True
    assert "strict" in rows["丙"].meta["total_evidence"]


def test_p1_position_never_alone():
    # 末行仅位置信号（无关键词、算术不符）→ 两个标都不打，也不产 warning
    from document2chunk.extractors.excel.parser import parse_excel_bytes
    from tests._excel_fixtures import build_xlsx

    data = build_xlsx({"s": [["名称", "数量"], ["甲", 1], ["乙", 2], ["备注", 7]]})
    result = parse_excel_bytes(data, "t.xlsx")
    rows = {r.data["名称"]: r for r in result.rows}
    assert "from_total" not in rows["备注"].meta
    assert "from_total_candidate" not in rows["备注"].meta
    assert not any("疑似合计" in w for w in result.warnings)


def test_p1_header_reject_long_text_row():
    # 19 号 region2/3 场景：首行是长句/URL 数据行 → 整区无表头，行内容全保留
    from document2chunk.extractors.excel.parser import parse_excel_bytes
    from tests._excel_fixtures import build_xlsx

    data = build_xlsx(
        {"s": [
            ["全省高速公路改扩建工程 2025 年度第三次调度会议纪要纪要纪要纪要", "http://example.com/meeting-2025"],
            ["会议名称", "参会人数"],
            ["第一季度调度会", 45],
        ]}
    )
    result = parse_excel_bytes(data, "t.xlsx")
    assert len(result.rows) == 3                      # 3 行全保留
    assert all(set(r.data) == {"列1", "列2"} for r in result.rows)  # 无表头 → 列N


def test_p1_header_banner_merged_row_exempt():
    # 横幅豁免：整行宽合并长标题不受拒升约束（既有行为：标题进表头路径第一级）
    from document2chunk.extractors.excel.parser import parse_excel_bytes
    from tests._excel_fixtures import build_xlsx

    data = build_xlsx(
        {"s": [
            ["XX 项目 R&D 经费决算表（单位：万元）——超长标题用于触发长度阈值判定的测试文本", "", "", ""],
            ["科目", "数量", "金额", "备注"],
            ["设备费", 1, 100, "x"],
        ]},
        merges=("A1:D1",),
    )
    result = parse_excel_bytes(data, "t.xlsx")
    keys = set().union(*(set(r.data) for r in result.rows))
    assert any("科目" in k for k in keys)             # 表头路径成立（横幅未被拒升，标题仍为键前缀——112 已知限制）
    assert not any(k.startswith("列") for k in keys)  # 未退化为无表头


def test_p1_pure_list_no_header_promotion():
    # 02 号 Sheet1 场景：宽 1 纯字符串列无表头语义 → h=0，首项不得提升为键
    from document2chunk.extractors.excel.parser import parse_excel_bytes
    from tests._excel_fixtures import build_xlsx

    data = build_xlsx({"列表": [["甲类"], ["乙类"], ["丙类"]]})
    result = parse_excel_bytes(data, "t.xlsx")
    assert [r.data["列1"] for r in result.rows] == ["甲类", "乙类", "丙类"]
