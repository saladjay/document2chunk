"""postprocess 全文档后处理测试（designs/009 统一 BlockNode 核心）。

覆盖 4 个函数：filter_noise（三证据）/ merge_cross_page（续接+多行标题）/
calibrate_levels（栈序+doc_title+toc）/ split_attachments。以及 postprocess 入口集成。
"""

from __future__ import annotations

from document2chunk.postprocess import (
    calibrate_levels,
    filter_noise,
    merge_cross_page,
    postprocess,
    split_attachments,
)
from document2chunk.ir import (
    DocumentMetadata,
    HeadingNode,
    ParagraphNode,
    Provenance,
    SourceType,
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
