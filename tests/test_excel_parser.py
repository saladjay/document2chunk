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


def test_p1_header_long_column_notes_kept():
    # 14 号 canary 形态：表头行含 2/7 个 ≥30 字说明列名（软信号占比低）→ 表头保留
    from document2chunk.extractors.excel.parser import parse_excel_bytes
    from tests._excel_fixtures import build_xlsx

    header = [
        "序号", "项目编号", "立项年份", "项目来源", "承担单位",
        "成果应用推广情况（如成果应用到什么项目，应用推广情况，成果转化情况等说明）",
        "是否值得推广（项目成果可以转化为实际的生产力，进一步推动科技进步经济发展的说明）",
    ]
    data = build_xlsx(
        {"s": [header, [1, "2016B0101", 2016, "科技厅", "利通公司", "长文本内容一", "长文本内容二"]]}
    )
    result = parse_excel_bytes(data, "t.xlsx")
    assert len(result.rows) == 1
    assert set(result.rows[0].data) == set(header)


def test_p1_header_year_data_row_rejected():
    # 21 号形态：真表头下一行以裸年份开头 → 拒升止步 h=1，年份行按数据输出
    from document2chunk.extractors.excel.parser import parse_excel_bytes
    from tests._excel_fixtures import build_xlsx

    data = build_xlsx(
        {"s": [
            ["发文年份", "级别", "发文单位", "政策名称"],
            ["2015年", "国家", "中共中央办公厅 国务院办公厅",
             "中共中央办公厅 国务院办公厅印发《深化科技体制改革实施方案》全文内容较长较长较长较长较长较长"],
            ["2016年", "国家", "国务院", "国务院关于印发实施《中华人民共和国促进科技成果转化法》若干规定的通知通知通知"],
            ["2017年", "省级", "广东省科技厅", "广东省重点领域研发计划项目", 100],
        ]}
    )
    result = parse_excel_bytes(data, "t.xlsx")
    assert len(result.rows) == 3
    assert set(result.rows[0].data) == {"发文年份", "级别", "发文单位", "政策名称"}
    assert result.rows[0].data["发文年份"] == "2015年"


def test_p1_orphan_region_fallback_to_doc_blocks():
    # 04/09/07 场景：标题行/说明区自成一区 → 旧版 0 行 0 块无声消失，新版兜底成块
    from document2chunk.extractors.excel.parser import parse_excel_bytes
    from tests._excel_fixtures import build_xlsx

    # 空行隔断复刻 04 号真实结构；标题/尾注 ≥20 字走 doc 路线成块；
    # 第三区是孤表头带（0 数据行）→ data_table 路径 0 产出 → 兜底
    # （孤表头带前须隔 2 个空行：若三条带都是单空行间隔，MIN_CHAIN=3 的碎片回并
    #   会把它们粘成一个 DOC 区域，兜底路径反而无从触发）
    data = build_xlsx(
        {"s": [
            ["仁新项目智慧管养无人机巡检关键技术研究与应用 R&D 经费决算表（单位：万元）"],
            [],
            ["注意：本表数据需经财务部门复核确认，并加盖单位公章后于每周一前报送报送"],
            [],
            [],
            ["支出科目说明", "报送口径"],
        ]}
    )
    result = parse_excel_bytes(data, "t.xlsx")
    texts = [b.text for b in result.blocks]
    assert any("单位：万元" in t for t in texts)            # 标题行成块
    assert any(t.startswith("注意") for t in texts)         # 尾注成块
    assert any("支出科目说明" in t for t in texts)          # 孤表头兜底成块
    assert any("兜底" in w for w in result.warnings)        # 兜底必留痕


def test_p1_doc_sheet_header_rescue():
    # 18 号场景：doc 路线首行是真实内容（非短列标签）→ 救援成块
    from document2chunk.extractors.excel.parser import parse_excel_bytes
    from tests._excel_fixtures import build_xlsx

    data = build_xlsx(
        {"s": [
            ["科小星智能问答平台（一期验收）", "平台名称：智慧交通问答服务系统"],
            ["为什么上传后没有反应？请检查网络连接是否正常，或稍后重试试试看。", "已处理完成，若仍未生效请联系管理员核实账号权限配置情况。"],
        ]}
    )
    result = parse_excel_bytes(data, "t.xlsx")
    headers = [b for b in result.blocks if b.meta.get("role") == "sheet_header"]
    assert headers and "科小星" in headers[0].text


def test_p1_doc_sheet_short_labels_still_folded():
    # 11 号 FAQ 场景：短列标签（问题/答复）按设计折叠，不救援、不新增块
    from document2chunk.extractors.excel.parser import parse_excel_bytes
    from tests._excel_fixtures import build_xlsx

    data = build_xlsx(
        {"s": [
            ["问题", "答复"],
            ["为什么上传后没有反应？请检查网络连接是否正常，或稍后重试试试看。", "已处理完成，若仍未生效请联系管理员核实账号权限配置情况。"],
        ]}
    )
    result = parse_excel_bytes(data, "t.xlsx")
    assert not [b for b in result.blocks if b.meta.get("role") == "sheet_header"]
    assert len(result.blocks) == 1


def test_p1_key_structure_mismatch_warning():
    # 04 号场景：同 sheet 两个等宽表结构不同 → 告警
    from document2chunk.extractors.excel.parser import parse_excel_bytes
    from tests._excel_fixtures import build_xlsx

    data = build_xlsx(
        {"s": [
            ["科目", "金额"],
            ["设备费", 100],
            [], [],
            ["科目", "支出"],
            ["材料费", 50],
        ]}
    )
    result = parse_excel_bytes(data, "t.xlsx")
    assert any("表头结构不一致" in w for w in result.warnings)


def test_p1_error_literal_data_kept_text_chinese(monkeypatch):
    import document2chunk.extractors.excel.parser as parser_mod
    from document2chunk.extractors.excel.models import SheetGrid

    grid = SheetGrid(
        name="s",
        values=[["名称", "状态"], ["甲", ""], ["乙", ""]],
        error_cells={(1, 1): "#N/A", (2, 1): "#DIV/0!"},
    )
    monkeypatch.setattr(parser_mod, "read_sheet_grids", lambda data: ([grid], []))
    result = parser_mod.parse_excel_bytes(b"x", "t.xlsx")
    assert len(result.rows) == 2
    assert result.rows[0].data["状态"] == "#N/A"                     # data 保留原文
    assert "无可用值" in result.rows[0].text and "#N/A" not in result.rows[0].text
    assert "除零错误" in result.rows[1].text
    assert any("错误值单元格" in w for w in result.warnings)          # 计数告警


def test_p1_error_zh_table():
    from document2chunk.extractors.excel.values import error_zh

    assert error_zh("#N/A") == "无可用值"
    assert error_zh(" #N/A ") == "无可用值"
    assert error_zh("普通文本") == "普通文本"
    assert error_zh(42) == 42


def test_p1_totals_region_window_side_table():
    # 05 号形态：主表与侧表同物理行并排（隔 1 空列）——侧表合计不得被主表列稀释
    from document2chunk.extractors.excel.parser import parse_excel_bytes
    from tests._excel_fixtures import build_xlsx

    data = build_xlsx(
        {"s": [
            ["科目", "金额", None, "科目2", "金额2"],
            ["人员费", 100, None, "甲", 10],
            ["设备费", 200, None, "乙", 20],
            ["燃料费", 300, None, "合计", 30],
            ["合计", 600, None, None, None],
        ]}
    )
    result = parse_excel_bytes(data, "t.xlsx")
    main = [r for r in result.rows if r.data.get("科目") == "合计"]
    side = [r for r in result.rows if r.data.get("科目2") == "合计"]
    assert len(main) == 1 and main[0].meta.get("from_total") is True
    assert len(side) == 1, "侧表合计行必须作为行产出"
    assert side[0].meta.get("from_total") is True          # keyword+strict：窗内 30=10+20
    assert "keyword" in side[0].meta["total_evidence"]
    assert "strict" in side[0].meta["total_evidence"]


def test_p1_header_ratio_boundary_and_url_veto():
    # 软信号边界：长格占比恰 0.5 → 否决；URL 硬信号低占比（1/4 格）也一票否决
    from document2chunk.extractors.excel.parser import parse_excel_bytes
    from tests._excel_fixtures import build_xlsx

    # 每个长格 36 字（≥30 阈值）：6 格中 3 长 = 恰 0.5 → 否决 → 整区无表头
    long_a = "长表头说明文字长表头说明文字长表头说明文字长表头说明文字长表头说明文字一"
    long_b = "长表头说明文字长表头说明文字长表头说明文字长表头说明文字长表头说明文字二"
    long_c = "长表头说明文字长表头说明文字长表头说明文字长表头说明文字长表头说明文字三"
    data = build_xlsx({"s": [
        [long_a, "k1", long_b, "k2", long_c, "k3"],
        ["v1", 1, "v2", 2, "v3", 3],
    ]})
    result = parse_excel_bytes(data, "t.xlsx")
    assert len(result.rows) == 2
    assert all(set(r.data) == {"列1", "列2", "列3", "列4", "列5", "列6"} for r in result.rows)

    # URL 硬信号：候选表头行含 URL（占比仅 1/4）也否决 → 整区无表头，「链接」不得成键
    data2 = build_xlsx({"s": [
        ["名称", "链接", "https://example.com/x", "备注"],
        ["甲", "https://example.com/x", "A", "b"],
        ["乙", "https://example.com/y", "B", "c"],
    ]})
    result2 = parse_excel_bytes(data2, "t.xlsx")
    assert len(result2.rows) == 3
    assert all("链接" not in r.data for r in result2.rows)
