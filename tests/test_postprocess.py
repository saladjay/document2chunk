"""postprocess 全文档后处理测试（designs/009 统一 BlockNode 核心）。

覆盖 4 个函数：filter_noise（三证据）/ merge_cross_page（续接+多行标题）/
calibrate_levels（栈序+doc_title+toc）/ split_attachments。以及 postprocess 入口集成。
"""

from __future__ import annotations

from document2chunk.postprocess import (
    calibrate_levels,
    filter_noise,
    merge_cross_page,
    merge_split_tables,
    postprocess,
    split_attachments,
)
from document2chunk.ir import (
    DocumentMetadata,
    HeadingNode,
    ParagraphNode,
    Provenance,
    SourceType,
    TableCellNode,
    TableRowNode,
    TableNode,
    TocEntry,
)

OCR = SourceType.OCR


# ── 构造辅助 ──

def _prov(bbox, page=0):
    return Provenance(source_type=OCR, page_index=page, bbox=list(bbox))


def P(text, page=0, bbox=(0, 0, 100, 20)):
    return ParagraphNode(id=f"p{text[:4]}{page}{bbox[1]}", text=text, provenance=_prov(bbox, page))


def H(text, level=1, page=0, bbox=(0, 0, 100, 20)):
    return HeadingNode(id=f"h{text[:4]}{page}", level=level, text=text, provenance=_prov(bbox, page))


def _md():
    return DocumentMetadata(source_type=OCR)


GEO = {0: (595, 842), 1: (595, 842), 2: (595, 842), 3: (595, 842), 4: (595, 842)}


# ══════════════════════════════════════
#  calibrate_levels
# ══════════════════════════════════════

def test_calibrate_doc_title_by_height():
    """无编号 HeadingNode 高度 ≥ body_h×1.8 → H1 + metadata.title。"""
    content = [
        H("某某关于改革完善管理的通知", level=2, bbox=(0, 0, 100, 40)),
        P("正文一", bbox=(0, 0, 100, 20)),
        P("正文二", bbox=(0, 0, 100, 20)),
    ]
    md = _md()
    out = calibrate_levels(content, md)
    heads = [(b.level, b.text) for b in out if isinstance(b, HeadingNode)]
    assert heads == [(1, "某某关于改革完善管理的通知")], heads
    assert md.title == "某某关于改革完善管理的通知"


def test_calibrate_doc_title_secondary_to_custom():
    """多个无编号大标题：最长→title，其余→custom['doc_titles'] 降级 Paragraph。"""
    content = [
        H("版头机关名称", level=1, bbox=(0, 0, 100, 40)),
        H("某某关于改革完善管理的通知全文", level=2, bbox=(0, 0, 100, 40)),
        P("正文", bbox=(0, 0, 100, 20)),
    ]
    md = _md()
    out = calibrate_levels(content, md)
    assert md.title == "某某关于改革完善管理的通知全文"
    assert md.custom.get("doc_titles") == ["版头机关名称"]
    assert "版头机关名称" in [b.text for b in out if isinstance(b, ParagraphNode)]


def test_calibrate_doc_title_fallback_no_height():
    """无高度大标题：首个无编号 H1/H2 + len≥8 → doc_title（fallback）。"""
    content = [
        H("某某关于加大耕地提质改造力度的通知", level=2, bbox=(0, 0, 100, 20)),
        H("一、总则", level=1, bbox=(0, 0, 100, 20)),
        P("正文", bbox=(0, 0, 100, 20)),
    ]
    md = _md()
    out = calibrate_levels(content, md)
    assert md.title == "某某关于加大耕地提质改造力度的通知"
    heads = {b.text: b.level for b in out if isinstance(b, HeadingNode)}
    assert heads["某某关于加大耕地提质改造力度的通知"] == 1
    assert heads["一、总则"] == 2


def test_calibrate_style_stack_with_doc_title():
    """有大标题：一、→H2，（一）→H3（level_offset=1）。"""
    content = [
        H("文章标题", level=1, bbox=(0, 0, 100, 40)),
        H("一、第一章", level=1, bbox=(0, 0, 100, 20)),
        P("正文", bbox=(0, 0, 100, 20)),
        H("（一）子项", level=1, bbox=(0, 0, 100, 20)),
        P("正文二", bbox=(0, 0, 100, 20)),
    ]
    md = _md()
    out = calibrate_levels(content, md)
    heads = {b.text: b.level for b in out if isinstance(b, HeadingNode)}
    assert heads["一、第一章"] == 2
    assert heads["（一）子项"] == 3


def test_calibrate_style_stack_no_doc_title():
    """无大标题：一、→H1，（一）→H2。"""
    content = [
        H("一、第一章", level=1, bbox=(0, 0, 100, 20)),
        P("正文", bbox=(0, 0, 100, 20)),
        H("（一）子项", level=1, bbox=(0, 0, 100, 20)),
        P("正文二", bbox=(0, 0, 100, 20)),
    ]
    md = _md()
    out = calibrate_levels(content, md)
    heads = {b.text: b.level for b in out if isinstance(b, HeadingNode)}
    assert heads["一、第一章"] == 1
    assert heads["（一）子项"] == 2


def test_calibrate_appendix_resets_stack():
    """附表/附件/附录 → 重置栈，后续编号从 H1 重新计数。"""
    content = [
        H("一、正文章节", level=1, bbox=(0, 0, 100, 20)),
        P("正文", bbox=(0, 0, 100, 20)),
        H("附表：汇总表", level=2, bbox=(0, 0, 100, 20)),
        H("一、附件子项", level=1, bbox=(0, 0, 100, 20)),
        P("附件正文", bbox=(0, 0, 100, 20)),
    ]
    md = _md()
    out = calibrate_levels(content, md)
    heads = {b.text: b.level for b in out if isinstance(b, HeadingNode)}
    assert heads["附表：汇总表"] == 1
    assert heads["一、附件子项"] == 1


def test_calibrate_doc_title_promotes_paragraph_r2():
    """R2：高比例居中 ParagraphNode（无编号）→ 提升为 HeadingNode H1。"""
    content = [P("自然资源部关于改革完善管理的通知", bbox=(0, 0, 400, 45))]
    for i in range(4):
        content.append(P("正文内容文字填充" * 3, bbox=(0, 100 + i * 25, 400, 120 + i * 25)))
    md = _md()
    out = calibrate_levels(content, md)
    assert md.title == "自然资源部关于改革完善管理的通知"
    assert any(isinstance(b, HeadingNode) and b.level == 1 for b in out)


def test_calibrate_with_toc_entries_override():
    """toc_entries 精确匹配 → 覆盖栈序定级；并回写 TocEntry.level。"""
    toc = [
        TocEntry(text="第一章 总则", level=None, page=1),
        TocEntry(text="第二章 附则", level=None, page=5),
    ]
    content = [
        H("文章标题", level=1, bbox=(0, 0, 100, 40)),
        H("第一章 总则", level=1, bbox=(0, 0, 100, 20)),
        P("正文", bbox=(0, 0, 100, 20)),
        H("第二章 附则", level=1, bbox=(0, 0, 100, 20)),
    ]
    md = _md()
    out = calibrate_levels(content, md, toc_entries=toc)
    heads = {b.text: b.level for b in out if isinstance(b, HeadingNode)}
    # toc 无自带 level，build_toc_mapping 用 depth：第一章 depth=1→2，第二章 depth=1→2
    assert heads["第一章 总则"] == 2
    assert heads["第二章 附则"] == 2
    # 回写：toc 条目拿到 level
    assert toc[0].level is not None


# ══════════════════════════════════════
#  merge_cross_page
# ══════════════════════════════════════

def test_merge_cross_page_continuation():
    """page N 末段无句号 + page N+1 首段 → join。"""
    content = [
        P("这是一段没有结束的内容", page=0, bbox=(0, 800, 100, 820)),
        P("继续的下一段文字", page=1, bbox=(0, 0, 100, 20)),
    ]
    out = merge_cross_page(content)
    paras = [b for b in out if isinstance(b, ParagraphNode)]
    assert len(paras) == 1
    assert "继续的下一段文字" in paras[0].text


def test_merge_cross_page_blocked_by_period():
    """page N 末段以句号结尾 → 不 join。"""
    content = [
        P("这是一段完整的内容。", page=0, bbox=(0, 800, 100, 820)),
        P("下一段独立内容", page=1, bbox=(0, 0, 100, 20)),
    ]
    out = merge_cross_page(content)
    assert len([b for b in out if isinstance(b, ParagraphNode)]) == 2


def test_merge_cross_page_blocked_by_intervening_heading():
    """design 006 §4.2：仅相邻 ParagraphNode 对可续接——中间隔标题不拼。

    真实案例（TB10182-2017）：page2 末段落款日期 + page3 首块标题「前言」+
    page3 首段正文，旧实现按「页末段/页首段」配对，跨标题把落款日期粘进正文。
    """
    content = [
        P("2017年12月22日", page=2, bbox=(0, 700, 100, 720)),
        H("前言", level=2, bbox=(0, 100, 100, 130)),
        P("本规程是在总结实践经验的基础上编制完成的。", page=3, bbox=(0, 140, 100, 200)),
    ]
    out = merge_cross_page(content)
    paras = [b for b in out if isinstance(b, ParagraphNode)]
    assert len(paras) == 2, [p.text for p in paras]
    assert paras[0].text == "2017年12月22日"


def test_merge_cross_page_blocked_by_intervening_list():
    """同上：中间隔 ListNode（整页编号列表）不拼。"""
    from document2chunk.ir import ListItemNode, ListNode, Provenance, SourceType

    lst = ListNode(
        id="l1", ordered=True,
        items=[ListItemNode(id="li1", level=0, blocks=[P("1. 明确了适用范围。")]),
               ListItemNode(id="li2", level=0, blocks=[P("2. 规定了控制标准。")])],
        provenance=Provenance(source_type=SourceType.OCR, page_index=3, bbox=[0, 300, 100, 400]),
    )
    content = [
        P("……主要内容如下：", page=3, bbox=(0, 200, 100, 220)),
        lst,
        P("本规程执行过程中，希望各单位结合工程实践。", page=4, bbox=(0, 0, 100, 60)),
    ]
    out = merge_cross_page(content)
    paras = [b for b in out if isinstance(b, ParagraphNode)]
    assert len(paras) == 2, [p.text for p in paras]
    assert paras[0].text.endswith("主要内容如下：")


def test_merge_cross_page_blocked_by_clause_number():
    """条文号段落（x.y.z 形，5.0.1/5.0.3）是子条文标题，不跨页续接。

    真实案例（TB10182-2017 p41/p51）：旧实现把「5.0.1 桩板结构形式」与次页
    「5.0.3 由于桩板结构……」拼成一段。R10 的 _LIST_MARKER_RE 因 (?!\d)
    前瞻放行多级编号，此为其盲区。
    """
    content = [
        P("5.0.1 桩板结构形式", page=41, bbox=(0, 700, 100, 720)),
        P("5.0.3 由于桩板结构下穿…", page=42, bbox=(0, 0, 100, 20)),
        P("（2005.5～2006.6）", page=50, bbox=(0, 700, 100, 720)),
        P("8.0.6 变形缝是隧道…", page=51, bbox=(0, 0, 100, 20)),
    ]
    out = merge_cross_page(content)
    paras = [b.text for b in out if isinstance(b, ParagraphNode)]
    assert paras == ["5.0.1 桩板结构形式", "5.0.3 由于桩板结构下穿…",
                     "（2005.5～2006.6）", "8.0.6 变形缝是隧道…"]


def test_merge_cross_page_decimal_continuation_still_joins():
    """小数尺寸（0.75 m、1.25 m）不是条文号，仍正常续接。"""
    content = [
        P("……分别不宜小于0.75 m、1.0 m、", page=33, bbox=(0, 700, 100, 720)),
        P("1.25 m、1.5 m。", page=34, bbox=(0, 0, 100, 20)),
    ]
    out = merge_cross_page(content)
    paras = [b for b in out if isinstance(b, ParagraphNode)]
    assert len(paras) == 1
    assert "1.25 m" in paras[0].text


def test_merge_cross_page_multiline_heading():
    """多行无编号标题合并（_merge_headings 迁入）：相邻同 level + 前段无句尾 → 合并。"""
    content = [
        H("广东省自然资源厅关于印发《广东省补充", level=2, bbox=(0, 0, 100, 20)),
        H("耕地指标交易管理办法》的通知", level=2, bbox=(0, 20, 100, 40)),
        P("正文", bbox=(0, 40, 100, 60)),
    ]
    out = merge_cross_page(content)
    heads = [b.text for b in out if isinstance(b, HeadingNode)]
    assert len(heads) == 1
    assert "耕地指标交易管理办法》的通知" in heads[0]


# ══════════════════════════════════════
#  filter_noise（三证据）
# ══════════════════════════════════════

def test_filter_noise_cross_page_header_repeat():
    """跨页重复页眉（≥3 页、≥10 字符）→ 移除；正文保留。"""
    bodies = ["耕地保护的具体措施包括严格的审批流程。",  # 每页正文实质不同（非仅页号差异）
              "占补平衡需要省级统筹安排落实指标。",
              "永久基本农田的划定应当符合规划要求。"]
    content = []
    for pg in range(3):
        content.append(P("国土资源部关于通知文件", page=pg, bbox=(0, 0, 100, 10)))  # 顶部页眉
        content.append(P(bodies[pg], page=pg, bbox=(0, 100, 500, 820)))
    out = filter_noise(content, page_geometry=GEO)
    texts = [b.text for b in out if isinstance(b, ParagraphNode)]
    assert "国土资源部关于通知文件" not in texts
    assert all(b in texts for b in bodies)


def test_filter_noise_no_blind_strip_single_page():
    """R9：单页顶部内容（未跨页重复）→ 不移除（绝不盲删顶/底带）。"""
    content = [
        P("唯一的首段标题文字", page=0, bbox=(0, 0, 300, 15)),
        P("正文内容" * 10, page=0, bbox=(0, 100, 500, 820)),
    ]
    out = filter_noise(content, page_geometry=GEO)
    assert "唯一的首段标题文字" in [b.text for b in out if isinstance(b, ParagraphNode)]


def test_filter_noise_page_number_pure_digits():
    """R4：底部纯数字页码 1,2,3,4 形成序列 → 移除。"""
    bodies = ["耕地保护的具体措施包括严格的审批流程和监督机制。",
              "占补平衡需要省级统筹安排落实指标核销工作。",
              "永久基本农田的划定应当符合土地利用总体规划。",
              "土地整治项目应当优先保障粮食生产用地需求。"]
    content = []
    for pg in range(1, 5):
        content.append(P(str(pg), page=pg, bbox=(280, 810, 310, 820)))  # 底部窄页码
        content.append(P(bodies[pg - 1], page=pg, bbox=(0, 100, 500, 820)))
    out = filter_noise(content, page_geometry=GEO)
    texts = [b.text for b in out if isinstance(b, ParagraphNode)]
    assert not any(t in ("1", "2", "3", "4") for t in texts)
    assert all(b in texts for b in bodies)


def test_filter_noise_page_number_fraction():
    """R4：分数式页码 321/322,322/323,323/324 → 移除。"""
    bodies = ["耕地保护的具体措施包括严格的审批流程和监督机制。",
              "占补平衡需要省级统筹安排落实指标核销工作。",
              "永久基本农田的划定应当符合土地利用总体规划。",
              "土地整治项目应当优先保障粮食生产用地需求。"]
    content = []
    fracs = ["321/322", "322/323", "323/324", "324/325"]
    for pg, f in enumerate(fracs, start=1):
        content.append(P(f, page=pg, bbox=(280, 810, 320, 820)))
        content.append(P(bodies[pg - 1], page=pg, bbox=(0, 100, 500, 820)))
    out = filter_noise(content, page_geometry=GEO)
    texts = [b.text for b in out if isinstance(b, ParagraphNode)]
    assert not any(t in fracs for t in texts)


def test_filter_noise_table_bottom_number_not_removed():
    """表格底部合计数字（不形成跨页递增序列）→ 不误删。"""
    bodies = ["耕地保护的具体措施包括严格的审批流程和监督机制。",
              "占补平衡需要省级统筹安排落实指标核销工作。",
              "永久基本农田的划定应当符合土地利用总体规划。"]
    content = []
    for pg in range(3):
        content.append(P(f"合计金额{1000 + pg}", page=pg, bbox=(280, 810, 400, 820)))
        content.append(P(bodies[pg], page=pg, bbox=(0, 100, 500, 820)))
    out = filter_noise(content, page_geometry=GEO)
    texts = [b.text for b in out if isinstance(b, ParagraphNode)]
    # 合计金额不是纯数字页码格式（含中文）→ 保留
    assert sum("合计金额" in t for t in texts) == 3


def test_filter_noise_single_extreme_bottom_page_number():
    """R4 兜底：单一极端底部窄页码（无跨页序列，如 HTML 版 PDF 只标 1/3）→ 移除。"""
    body = "正文段落内容填充文字略长一些以撑起页面高度。"
    content = [
        P(body, page=0, bbox=(0, 100, 500, 820)),
        P("1/3", page=0, bbox=(558, 819, 570, 828)),  # 极端底部 + 窄（width 12 < 595*0.12）
    ]
    out = filter_noise(content, page_geometry={0: (595, 842)})
    texts = [b.text for b in out if isinstance(b, ParagraphNode)]
    assert "1/3" not in texts
    assert body in texts


# ══════════════════════════════════════
#  split_attachments
# ══════════════════════════════════════

def test_split_attachments_single():
    content = [H("一、正文章节", level=1), P("正文内容"), H("附件：申报表", level=1), P("附件内容")]
    main, attach = split_attachments(content)
    assert len(attach) == 1
    assert attach[0][0].text == "附件：申报表"


def test_split_attachments_multiple():
    content = [P("正文"), H("附表：表一", level=1), P("一"), H("附表：表二", level=1), P("二")]
    main, attach = split_attachments(content)
    assert len(attach) == 2


def test_split_attachments_none():
    content = [H("一、章节", level=1), P("正文")]
    main, attach = split_attachments(content)
    assert attach == [] and len(main) == 2


# ══════════════════════════════════════
#  merge_split_tables —— 多页重复表头合并
# ══════════════════════════════════════

def _tbl(header, rows, tid):
    """构造表格：header=[..], rows=[[..],[..]]。"""
    def row(cells, is_header=False):
        return TableRowNode(
            id=f"r{tid}_{is_header}",
            is_header=is_header,
            cells=[TableCellNode(id=f"c{tid}_{i}", blocks=[ParagraphNode(id=f"p{tid}_{i}", text=c)])
                   for i, c in enumerate(cells)],
        )
    all_rows = [row(header, is_header=True)] + [row(r) for r in rows]
    return TableNode(id=tid, rows=all_rows)


def test_merge_split_tables_same_header():
    """连续同表头表 → 合并成一张（保留首表头 + 所有数据行）。"""
    content = [
        _tbl(["序号", "事项"], [["1", "甲"], ["2", "乙"]], "t1"),
        _tbl(["序号", "事项"], [["3", "丙"], ["4", "丁"]], "t2"),
        _tbl(["序号", "事项"], [["5", "戊"]], "t3"),
    ]
    out = merge_split_tables(content)
    assert len(out) == 1, len(out)
    # 1 表头 + 5 数据行
    assert len(out[0].rows) == 6, len(out[0].rows)
    # 表头只一次
    assert out[0].rows[0].is_header
    assert all(not r.is_header for r in out[0].rows[1:])


def test_merge_split_tables_different_header():
    """表头不同的表不合并。"""
    content = [
        _tbl(["序号", "事项"], [["1", "甲"]], "t1"),
        _tbl(["A", "B"], [["x", "y"]], "t2"),
    ]
    out = merge_split_tables(content)
    assert len(out) == 2


def test_merge_split_tables_interrupted():
    """中间夹段落 → 只合并相邻同表头段。"""
    content = [
        _tbl(["序号", "事项"], [["1", "甲"]], "t1"),
        P("夹一段"),
        _tbl(["序号", "事项"], [["2", "乙"]], "t2"),
    ]
    out = merge_split_tables(content)
    assert len(out) == 3  # 不合并（被段落打断）


# ══════════════════════════════════════
#  postprocess 集成入口
# ══════════════════════════════════════

def test_postprocess_full_flow():
    """端到端：噪声过滤 + 跨页合并 + 定级 + 附件拆分一次完成。"""
    body0 = "耕地保护的具体措施包括严格的审批流程和监督机制。"
    body1 = "占补平衡需要省级统筹安排落实指标核销工作。"
    body2 = "永久基本农田的划定应当符合土地利用总体规划。"
    content = [
        # 页眉（跨3页重复→噪声）
        P("国土资源部关于通知文件", page=0, bbox=(0, 0, 100, 10)),
        # 文章标题（高比例→doc_title H1）
        H("某通知标题文字略长", level=2, page=0, bbox=(0, 50, 400, 95)),
        # 跨页段落（前段无句号 → 与 page1 首段合并）
        P("前段没有句号结束", page=0, bbox=(0, 100, 500, 820)),
        P("国土资源部关于通知文件", page=1, bbox=(0, 0, 100, 10)),
        P("后段续接内容", page=1, bbox=(0, 0, 100, 20)),
        P(body1, page=1, bbox=(0, 100, 500, 820)),
        P("国土资源部关于通知文件", page=2, bbox=(0, 0, 100, 10)),
        H("一、章节", level=1, page=2, bbox=(0, 50, 100, 70)),
        P(body2, page=2, bbox=(0, 100, 500, 820)),
        # 附件在新页顶部起头（y0=50 < 842×25%，符合 A 方案页顶判据）
        H("附件：附表内容", level=1, page=3, bbox=(0, 50, 100, 70)),
        P("附件正文", page=3, bbox=(0, 100, 500, 820)),
    ]
    md = _md()
    main, attach = postprocess(content, md, page_geometry=GEO, use_height_fallback=True)
    # 页眉被滤除
    assert all("国土资源部关于通知文件" != b.text for b in main if isinstance(b, ParagraphNode))
    # 跨页段落被合并
    assert any("后段续接内容" in (b.text or "") for b in main if isinstance(b, ParagraphNode))
    # 附件被拆出
    assert len(attach) == 1
    # 文章标题成 doc_title
    assert md.title is not None


# ══════════════════════════════════════
#  无 provenance（DOCX 路）None 安全
# ══════════════════════════════════════

def test_filter_noise_no_provenance_noop():
    """DOCX 块（provenance=None）经 filter_noise 不删不改。"""
    blocks = [
        H("某标题", level=1),
        P("正文内容比较长的一段时间"),
        P("321"),
    ]
    for b in blocks:
        b.provenance = None
    out = filter_noise(blocks, layout_data=None, page_geometry=None)
    assert [b.id for b in out] == [b.id for b in blocks]


def test_merge_cross_page_no_provenance_noop():
    """无页码的块不做跨页续接，也不做多行标题合并（DOCX 无页概念，
    相邻独立标题不应被拼坏——spec §4.5；PDF/OCR 有 provenance 不受影响）。"""
    blocks = [
        H("某标题前半", level=1),
        H("某标题后半", level=1),
        P("正文内容比较长的一段时间"),
    ]
    for b in blocks:
        b.provenance = None
    out = merge_cross_page(blocks)
    texts = [getattr(b, "text", "") for b in out]
    assert "正文内容比较长的一段时间" in texts      # 不拼接
    assert "某标题前半" in texts and "某标题后半" in texts  # 不合并
    assert len(out) == 3


def test_split_attachments_no_geometry():
    """page_geometry=None 时按文本正则正常拆分。"""
    blocks = [
        H("主标题", level=1),
        P("正文段落内容"),
        H("附件1：某某表格", level=1),
        P("附件内容"),
    ]
    main, segs = split_attachments(blocks, page_geometry=None)
    assert [b.text for b in main] == ["主标题", "正文段落内容"]
    assert len(segs) == 1
    assert [b.text for b in segs[0]] == ["附件1：某某表格", "附件内容"]


# ══════════════════════════════════════
#  DOCX 分支：字号比 doc_title + 样式层级权威 + 栈首见精化
# ══════════════════════════════════════

from document2chunk.ir import RunNode, RunProperties


def _drun(text, size):
    return RunNode(id=f"r{text[:3]}{size}", text=text,
                   style=RunProperties(font_size=size))


def _dp(text, size=16.0, centered=False, **kw):
    """DOCX 形态段落：无 provenance，带字号 runs。"""
    md = {"centered": True} if centered else {}
    md.update(kw)
    return ParagraphNode(id=f"dp{text[:4]}{size}", text=text,
                         runs=[_drun(text, size)], metadata=md)


def _dh(text, level, size=16.0, source=None, centered=False):
    """DOCX 形态标题：无 provenance，带字号 runs 与 heading_source。"""
    md = {}
    if source:
        md["heading_source"] = source
    if centered:
        md["centered"] = True
    return HeadingNode(id=f"dh{text[:4]}{level}", level=level, text=text,
                       runs=[_drun(text, size)], metadata=md)


def _mdocx():
    return DocumentMetadata(source_type=SourceType.DOCX)


def test_docx_doc_title_by_font_ratio():
    """首个居中大字号段落（22pt vs 正文 16pt）→ H1 + metadata.title。"""
    content = [
        _dp("某某关于改革完善占补平衡管理的通知", size=22.0, centered=True),
        _dp("正文第一段内容", size=16.0),
        _dp("正文第二段内容", size=16.0),
    ]
    md = _mdocx()
    out = calibrate_levels(content, md, use_height_fallback=False, body_font_size=16.0)
    heads = [(b.level, b.text) for b in out if isinstance(b, HeadingNode)]
    assert heads == [(1, "某某关于改革完善占补平衡管理的通知")], heads
    assert md.title == "某某关于改革完善占补平衡管理的通知"


def test_docx_style_level_authoritative():
    """heading_source=style 的层级保留，不被编号栈改写。"""
    content = [
        _dh("二级样式标题", level=2, size=16.0, source="style"),
        _dh("一、无样式编号段", level=2, size=16.0, source="heuristic"),
        _dp("正文内容", size=16.0),
    ]
    md = _mdocx()
    out = calibrate_levels(content, md, use_height_fallback=False, body_font_size=16.0)
    heads = [(b.level, b.text) for b in out if isinstance(b, HeadingNode)]
    # 样式 H2 保留；伪标题首见 cn_major 从 prev_level+1 起 → H3（issues5 场景）
    assert heads == [(2, "二级样式标题"), (3, "一、无样式编号段")], heads


def test_docx_stack_first_seen_from_prev():
    """首见编号样式的分配 = max(next_style_level, prev_level+1)。"""
    content = [
        _dh("无编号主标题若干字以上才像标题", level=1, size=16.0),  # 无 source → 走栈/回退路径
        _dh("一、第一部分", level=2, size=16.0, source="heuristic"),
        _dh("二、第二部分", level=2, size=16.0, source="heuristic"),
        _dh("（一）子项甲", level=2, size=16.0, source="heuristic"),
    ]
    md = _mdocx()
    out = calibrate_levels(content, md, use_height_fallback=False, body_font_size=16.0)
    heads = [(b.level, b.text) for b in out if isinstance(b, HeadingNode)]
    assert heads == [
        (1, "无编号主标题若干字以上才像标题"),
        (2, "一、第一部分"),
        (2, "二、第二部分"),
        (3, "（一）子项甲"),
    ], heads


def test_docx_style_siblings_consistent_with_doc_title():
    """doc_title(offset=1) 下两个同级样式标题层级一致（豁免跳跃钳制）。"""
    content = [
        _dp("某某关于改革完善管理的通知", size=22.0, centered=True),
        _dh("二级样式标题甲", level=2, size=16.0, source="style"),
        _dp("正文一段。", size=16.0),
        _dh("二级样式标题乙", level=2, size=16.0, source="style"),
        _dp("正文二段。", size=16.0),
    ]
    md = _mdocx()
    out = calibrate_levels(content, md, use_height_fallback=False, body_font_size=16.0)
    heads = [(b.level, b.text) for b in out if isinstance(b, HeadingNode)]
    assert heads == [
        (1, "某某关于改革完善管理的通知"),
        (3, "二级样式标题甲"),
        (3, "二级样式标题乙"),
    ], heads


def test_postprocess_docx_entry():
    """postprocess 入口透传 body_font_size，全链路对 DOCX 形态输入不崩。"""
    content = [
        _dp("某某文件的通知标题很长超过八个字符", size=22.0, centered=True),
        _dh("一、总体要求", level=2, size=16.0, source="heuristic"),
        _dp("正文内容一段。", size=16.0),
        _dh("附件1：附表", level=2, size=16.0, source="heuristic"),
        _dp("附件里的正文。", size=16.0),
    ]
    md = _mdocx()
    main, segs = postprocess(content, md, use_height_fallback=False, body_font_size=16.0)
    assert md.title == "某某文件的通知标题很长超过八个字符"
    assert any(isinstance(b, HeadingNode) and b.level == 2 and b.text == "一、总体要求" for b in main)
    assert len(segs) == 1
    assert segs[0][0].text == "附件1：附表"


if __name__ == "__main__":
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS {fn.__name__}")
            passed += 1
        except Exception as e:
            import traceback
            print(f"  FAIL {fn.__name__}: {type(e).__name__}: {e}")
            traceback.print_exc(limit=1)
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
