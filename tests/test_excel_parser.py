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
