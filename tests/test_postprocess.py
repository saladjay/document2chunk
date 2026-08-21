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


def test_calibrate_doc_title_position_constraint():
    """doc_title 位置约束（issues6 P0 #2）：候选限文档前 K 块——FAQ 类文档
    文中无编号长问句不得凭长度碾压文档开头的短真标题。"""
    long_q = "单位科技管理员或领导在审核过程中发现的问题应当如何进行处理和反馈呢"
    content = [
        H("常见问题解答", level=1, bbox=(0, 0, 100, 40)),
        P("问题一", bbox=(0, 0, 100, 20)),
    ] + [P(f"答案要点{i}", bbox=(0, 0, 100, 20)) for i in range(10)] + [
        H(long_q, level=1, bbox=(0, 0, 100, 40)),
        P("答案", bbox=(0, 0, 100, 20)),
    ]
    md = _md()
    out = calibrate_levels(content, md)
    assert md.title == "常见问题解答"
    # 长问句未被卷入 doc_title 竞争：保持 Heading，不被降级成 Paragraph
    assert long_q in [b.text for b in out if isinstance(b, HeadingNode)]


def test_calibrate_doc_title_position_fallback_keeps_late():
    """前 K 块无候选时保留原候选集（标题确实靠后的文档不丢 doc_title）。"""
    late_title = "某某关于加大耕地提质改造工作力度的实施意见全文标题"
    content = [P(f"版头第{i}行", bbox=(0, 0, 100, 20)) for i in range(12)] + [
        H(late_title, level=1, bbox=(0, 0, 100, 40)),
        P("正文", bbox=(0, 0, 100, 20)),
    ]
    md = _md()
    out = calibrate_levels(content, md)
    assert md.title == late_title
    assert late_title in [b.text for b in out if isinstance(b, HeadingNode)]


def test_calibrate_doc_title_fallback_early_short_title():
    """（issues6 P0 #2 真实样本·附件1汇总）无字号/居中证据（body_font_size=None、
    无 provenance）时走窄 fallback：块 0 的 6 字真标题「常见问题解答」应胜过
    块 9 的 39 字无编号长问句——早窗（前 3 块）短标题例外于 len≥8。"""
    long_q = "单位科技管理员或领导在审核过程中，发现没有提交或者退回的选择按钮，是什么原因？"
    content = [
        _dh("常见问题解答", level=1, size=22.0, source="style"),          # 块 0：真标题（6 字）
        _dh("1.忘记密码，该怎么办？", level=1, size=22.0, source="style"),
        _dp("【解决办法1】登录平台时，点击【忘记密码】，可利用账号绑定手机进行密码重置。"),
        _dp("【解决办法2】联系单位科技管理员，进行密码重置。"),
        _dh("2.项目第一负责人离职或者调去其他单位，想申请调整项目负责人，怎么办？",
            level=1, size=22.0, source="style"),
        _dp("【解决办法】交通厅立项项目项目负责人不可修改。"),
        _dh("3.清单项目什么时候申报？怎么申报？", level=1, size=22.0, source="style"),
        _dp("【解决办法】项目需先完成集团立项程序。"),
        _dp("再根据每年省厅发布的填报通知，在填报周期内利用本平台清单项目管理功能。"),
        _dh(long_q, level=1, size=22.0, source="style"),                  # 块 9：错误候选
        _dp("【解释】系统设置部分业务，在上传完规定的材料后，才可以提交或退回。"),
    ]
    md = _mdocx()
    out = calibrate_levels(content, md, use_height_fallback=False, body_font_size=None)
    assert md.title == "常见问题解答"
    heads = {b.text: b.level for b in out if isinstance(b, HeadingNode)}
    assert heads["常见问题解答"] == 1
    # 长问句保持 Heading，未被卷入 doc_title 竞争降级
    assert long_q in heads


def test_calibrate_doc_title_promotion_outranks_weak_fallback():
    """（issues6 P0 #2 真实样本·FAQ一）真标题是块 0 的 2.48× 大字号段落（未居中）；
    窄 fallback 会抓到块 13 的无编号长问句（含「规定」关键词、字号比仅 1.0、
    在前 10 块窗外）。段落提升（强证据）必须先于窄 fallback（弱证据）执行。"""
    long_q = "【解释】系统设置部分业务，在上传完规定的材料后，才可以提交或退回。"
    content = [
        _dp("常见问题解答", size=38.5),                                    # 块 0：真标题段落
    ] + [
        _dp(f"正文或问题内容填充第{i}行", size=15.5) for i in range(12)
    ] + [
        _dh(long_q, level=1, size=15.5, source="style"),                  # 块 13：弱 fallback 的错误选择
        _dp("【解决办法】检查审核内容填写的完整性。", size=15.5),
    ]
    md = _mdocx()
    out = calibrate_levels(content, md, use_height_fallback=False, body_font_size=15.5)
    assert md.title == "常见问题解答"
    heads = {b.text: b.level for b in out if isinstance(b, HeadingNode)}
    assert heads["常见问题解答"] == 1
    assert long_q in heads  # 长问句仍是标题（未被降级）


def test_calibrate_doc_title_toc_title_not_promoted():
    """（issues6 P0 #2 回归样本·粤交集基404号）真标题是 H1 但字号证据不足
    （15pt/14pt≈1.07，非居中→靠窄 fallback 命中）；「目 录」是居中大字段落
    （20pt/14pt≈1.43，在提升早窗内）。目录标题不得抢占 doc_title；早窗外
    的居中大字段落（审批表）一律不提升。"""
    title = "关于印发《广东省交通集团科技创新“十四五”发展纲要》的通知"
    content = [
        _dp("粤交集基〔2022〕404号", size=16.0),                       # 版头文号
        _dh(title, level=1, size=15.0, source="style"),                # 真标题（弱证据）
        _dp("直属各单位、本部各部门:", size=14.0),
        _dp("为适应新形势下的创新驱动发展需求，进一步增强集团科技创新能力。", size=14.0),
        _dp("附件：广东省交通集团科技创新“十四五”发展纲要", size=14.0),
        _dp("目 录", size=20.0, centered=True),                        # 目录页标题（早窗内居中大字）
    ] + [
        _dp(f"正文内容填充第{i}段，保持常规字号。", size=14.0) for i in range(10)
    ] + [
        _dp("审 批 表", size=20.0, centered=True),                     # 早窗外的居中大字（非标题）
        _dp("第一章 总则 ………………………………………………… 1", size=14.0),
    ]
    md = _mdocx()
    out = calibrate_levels(content, md, use_height_fallback=False, body_font_size=14.0)
    assert md.title == title
    # 目录标题未被提升为 HeadingNode；早窗外大字段落保持 ParagraphNode（断返回值 out，
    # 非——calibrate 不原位替换 content 元素，断 content 是恒真空转）
    assert all(b.text != "目 录" for b in out if isinstance(b, HeadingNode))
    assert all(isinstance(b, ParagraphNode) for b in out if b.text == "审 批 表")


def test_calibrate_doc_title_promotion_formula_runs_no_crash():
    """（799 样本回归·中期技术报告）前 10 块内含行内公式的段落被提升时，
    runs 里的 InlineFormulaNode 必须被过滤——HeadingNode.runs 只收 RunNode，
    不过滤即 ValidationError 整文件崩溃。"""
    from document2chunk.ir import InlineFormulaNode
    formula_para = ParagraphNode(
        id="p-formula", text="基于能量守恒的变形计算式",
        runs=[
            _drun("基于能量守恒的变形计算式", 22.0),
            InlineFormulaNode(id="f1", latex="E=mc^2"),
        ],
        metadata={"centered": True},
    )
    content = [
        formula_para,
        _dp("正文内容填充第一段。", size=14.0),
        _dp("正文内容填充第二段。", size=14.0),
    ]
    md = _mdocx()
    out = calibrate_levels(content, md, use_height_fallback=False, body_font_size=14.0)
    assert md.title == "基于能量守恒的变形计算式"
    h = [b for b in out if isinstance(b, HeadingNode) and b.text == "基于能量守恒的变形计算式"]
    assert h and h[0].level == 1
    assert all(r.type == "run" for r in h[0].runs)


def test_calibrate_doc_title_window_furniture_loses_to_weak_l1():
    """（审查场景 B·e80dac1 收窄）弱证据真标题（L1 样式、15pt/14pt≈1.07、非居中、
    len≥8）+ 块 5 居中 20pt 段落「审 批 表」——提升窗（前 10 块）内的强证据
    家具词抢占 doc_title，真标题被降 H2。修复：仅有提升候选时窄 fallback 候选
    并入同场竞争（_title_rank 定胜）——真标题凭文种关键词+长度反超家具词。"""
    title = "关于印发《广东省交通集团科技创新“十四五”发展纲要》的通知"
    content = [
        _dp("粤交集基〔2022〕404号", size=16.0),                       # 块 0：版头文号
        _dh(title, level=1, size=15.0, source="style"),                # 块 1：真标题（弱证据）
        _dp("直属各单位、本部各部门:", size=14.0),                       # 块 2
        _dp("为适应新形势下的创新驱动发展需求，进一步增强集团科技创新能力。", size=14.0),
        _dp("现将《广东省交通集团科技创新“十四五”发展纲要》印发给你们，请遵照执行。", size=14.0),
        _dp("审 批 表", size=20.0, centered=True),                     # 块 5：旧窗（<10）内家具词
    ] + [
        _dp(f"正文内容填充第{i}段，保持常规字号。", size=14.0) for i in range(10)
    ]
    md = _mdocx()
    out = calibrate_levels(content, md, use_height_fallback=False, body_font_size=14.0)
    assert md.title == title
    heads = {b.text: b.level for b in out if isinstance(b, HeadingNode)}
    assert heads[title] == 1
    # 家具词未被提升抢占，保持段落
    assert all(isinstance(b, ParagraphNode) for b in out if b.text == "审 批 表")


def test_calibrate_doc_title_early_short_l1_competes_in_early_window():
    """（审查场景 C·e80dac1 收窄）块 0 L1「使用说明」（4 字，早窗 len≥4 例外
    命中即 break）+ 块 2 L1「某某系统操作指引汇总文档」（12 字、无强证据）。
    早窗短 L1 不得 break 即胜——早窗内候选同场竞争（_title_rank），长真标题赢；
    早窗外（块 3+）仍是位置优先（FAQ二：块 9 长问句不得反转，另测覆盖）。"""
    content = [
        _dh("使用说明", level=1, size=16.0, source="style"),           # 块 0：短版头（4 字）
        _dp("本文档用于说明系统操作流程与注意事项。", size=16.0),
        _dh("某某系统操作指引汇总文档", level=1, size=16.0, source="style"),  # 块 2：长真标题
        _dp("正文内容第一段。", size=16.0),
        _dp("正文内容第二段。", size=16.0),
    ]
    md = _mdocx()
    out = calibrate_levels(content, md, use_height_fallback=False, body_font_size=16.0)
    assert md.title == "某某系统操作指引汇总文档"
    heads = {b.text: b.level for b in out if isinstance(b, HeadingNode)}
    assert heads["某某系统操作指引汇总文档"] == 1
    assert heads["使用说明"] == 2  # 早窗短版头退为下级标题


def test_calibrate_doc_title_early_short_l1_still_wins_over_late_long():
    """（FAQ二回归守卫·场景 C 收窄的边界）早窗短 L1（块 0「常见问题解答」6 字）
    对早窗外长 L1（块 9、39 字）仍是位置优先——同场竞争只发生在早窗（前 3 块）
    内，不得把 e80dac1 修复打回去。"""
    long_q = "单位科技管理员或领导在审核过程中，发现没有提交或者退回的选择按钮，是什么原因？"
    content = [
        _dh("常见问题解答", level=1, size=22.0, source="style"),       # 块 0：真标题（6 字）
        _dp("【解决办法】联系单位科技管理员，进行密码重置。", size=22.0),
        _dp("【解决办法】项目需先完成集团立项程序。", size=22.0),
    ] + [
        _dp(f"问题解答填充第{i}行。", size=22.0) for i in range(6)
    ] + [
        _dh(long_q, level=1, size=22.0, source="style"),               # 块 9：早窗外长 L1
        _dp("【解释】系统设置部分业务，在上传完规定的材料后，才可以提交或退回。", size=22.0),
    ]
    md = _mdocx()
    out = calibrate_levels(content, md, use_height_fallback=False, body_font_size=None)
    assert md.title == "常见问题解答"
    heads = {b.text: b.level for b in out if isinstance(b, HeadingNode)}
    assert heads["常见问题解答"] == 1
    assert long_q in heads  # 早窗外长 L1 不被卷入竞争降级


def test_calibrate_doc_title_demoted_promotion_restores_runs():
    """（Minor）doc_title 输家降级回 ParagraphNode 时须保留提升前的原始 runs——
    提升时为满足 HeadingNode.runs 只收 RunNode 做过过滤，降级段落可含
    InlineFormulaNode（ParagraphNode.runs 合法成员），过滤态不得带出去。"""
    from document2chunk.ir import InlineFormulaNode
    formula_para = ParagraphNode(
        id="p-loser", text="基于能量守恒的变形计算式",
        runs=[
            _drun("基于能量守恒的变形计算式", 22.0),
            InlineFormulaNode(id="f1", latex="E=mc^2"),
        ],
        metadata={"centered": True},
    )
    content = [
        formula_para,                                                  # 块 0：提升后在竞争中落败
        _dp("关于印发某某管理办法（试行）的通知", size=24.0, centered=True),  # 块 1：胜者（文种关键词+更长）
        _dp("正文内容填充第一段。", size=14.0),
    ]
    md = _mdocx()
    out = calibrate_levels(content, md, use_height_fallback=False, body_font_size=14.0)
    assert md.title == "关于印发某某管理办法（试行）的通知"
    loser = [b for b in out if getattr(b, "text", "") == "基于能量守恒的变形计算式"]
    assert loser and isinstance(loser[0], ParagraphNode)
    assert any(r.type == "inline_formula" for r in loser[0].runs)  # 原始 runs（含公式）还原


def test_calibrate_doc_title_ocr_promotion_window_and_toc_guard():
    """（Minor·OCR 路径与 DOCX 对齐）OCR 段落提升（高度比≥1.8）补两守卫：
    「目 录」整词不提升（窗内也如此）+ 仅前 _DOC_TITLE_MAX_BLOCK 块（块 11
    大字段落不提升）。两守卫挡的是**进入竞争**——若被提升后竞争落败，会以
    custom["doc_titles"] 留痕，故断其不出现。"""
    content = [
        P("自然资源部关于改革完善管理的通知", bbox=(0, 0, 400, 45)),   # 块 0：body_h=20 → 2.25 正常提升
        P("正文内容文字填充" * 3, bbox=(0, 100, 400, 120)),
        P("目 录", bbox=(0, 130, 400, 175)),                          # 块 2：目录标题（窗内整词，含关键词）
    ] + [
        P("正文内容文字填充" * 3, bbox=(0, 200 + i * 25, 400, 220 + i * 25)) for i in range(8)
    ] + [
        P("资料打印费预算明细表", bbox=(0, 420, 400, 465)),             # 块 11：窗外大字段落
        P("正文内容文字填充" * 3, bbox=(0, 500, 400, 520)),
    ]
    md = _md()
    out = calibrate_levels(content, md)
    assert md.title == "自然资源部关于改革完善管理的通知"
    losers = md.custom.get("doc_titles") or []
    assert "目 录" not in losers            # 整词守卫：根本未被提升入竞争
    assert "资料打印费预算明细表" not in losers  # 窗守卫：块 11 ≥ 10 未入竞争
    assert all(b.text != "目 录" for b in out if isinstance(b, HeadingNode))


def test_calibrate_doc_title_ocr_promotion_formula_runs_no_crash():
    """（OCR 形状·对齐 DOCX 路径）OCR _text_to_runs 产出 RunNode/InlineFormulaNode
    交替的 runs；高度比提升路径构造 HeadingNode 时原样透传 → 含公式 run 的段落
    被提升即 ValidationError 整文件崩溃（DOCX 路径 de50870 已过滤，OCR 路径漏）。"""
    from document2chunk.ir import InlineFormulaNode, RunNode
    formula_para = ParagraphNode(
        id="op-crash", text="总平面布置方案",
        runs=[RunNode(id="r1", text="总平面布置"), InlineFormulaNode(id="f1", latex="x")],
        provenance=_prov((0, 0, 400, 40)),                    # 高 40 / body 20 → 2.0 ≥ 1.8
    )
    content = [
        formula_para,
        P("正文内容文字填充" * 3, bbox=(0, 100, 400, 120)),
        P("正文内容文字填充" * 3, bbox=(0, 200, 400, 220)),
    ]
    md = _md()
    out = calibrate_levels(content, md)                       # use_height_fallback 默认 True
    assert md.title == "总平面布置方案"
    h = [b for b in out if isinstance(b, HeadingNode) and b.text == "总平面布置方案"]
    assert h and h[0].level == 1
    assert all(r.type == "run" for r in h[0].runs)            # 公式不进 heading runs


def test_calibrate_doc_title_ocr_demoted_promotion_restores_runs():
    """（OCR 形状·降级还原）OCR 提升路径过滤 runs 后，竞争中落败降级回
    ParagraphNode 须还原原始 runs——否则公式 run 永久丢失（对照 DOCX 路径
    promoted_orig_runs 的还原写法）。"""
    from document2chunk.ir import InlineFormulaNode, RunNode
    loser = ParagraphNode(
        id="op-loser", text="基于能量守恒的变形计算式",
        runs=[RunNode(id="r1", text="基于能量守恒的变形计算式"),
              InlineFormulaNode(id="f1", latex="E=mc^2")],
        provenance=_prov((0, 0, 400, 40)),
    )
    content = [
        loser,                                                   # 块 0：提升后竞争中落败
        P("关于印发某某管理办法试行的通知标题", bbox=(0, 60, 400, 100)),  # 块 1：胜者（文种关键词+更长）
        P("正文内容文字填充" * 3, bbox=(0, 200, 400, 220)),
        P("正文内容文字填充" * 3, bbox=(0, 300, 400, 320)),
        P("正文内容文字填充" * 3, bbox=(0, 400, 400, 420)),
    ]
    md = _md()
    out = calibrate_levels(content, md)
    assert md.title == "关于印发某某管理办法试行的通知标题"
    loser_out = [b for b in out if getattr(b, "text", "") == "基于能量守恒的变形计算式"]
    assert loser_out and isinstance(loser_out[0], ParagraphNode)
    assert any(r.type == "inline_formula" for r in loser_out[0].runs)  # 原始 runs（含公式）还原


def test_calibrate_doc_title_fallback_after_promoted_not_pooled():
    """（钉住并入位置前提）fallback 候选须**先于**首个提升候选才并入同场竞争：
    块 0 弱证据真标题（9 字）经 OCR 高比路径提升后，其后的无编号含文种长候选
    （len≥8、带「办法/通知」、前 10 块窗内）不得入池凭 _title_rank 反超——
    位置前于长度。几何对齐 :815 注释的查重智能体实证（块 0 提升 vs 块 1 问句）：
    提升块在早窗内本身即 fallback 早窗候选，紧随其后的长问句是唯一能被选中
    又落在提升者之后的 fallback 形状。"""
    title = "查重智能体测试问题"
    long_q = "单位管理员在审核过程中发现没有提交或者退回的处理办法和通知要求是什么呢"
    content = [
        P(title, bbox=(0, 0, 400, 45)),                        # 块 0：45/20=2.25 → 提升
        H(long_q, level=1, bbox=(0, 100, 400, 120)),           # 块 1：fallback 候选（高度比不足）
        P("正文内容文字填充" * 3, bbox=(0, 150, 400, 170)),
        P("正文内容文字填充" * 3, bbox=(0, 200, 400, 220)),
        P("正文内容文字填充" * 3, bbox=(0, 250, 400, 270)),
    ]
    md = _md()
    out = calibrate_levels(content, md)
    assert md.title == title                                   # fallback 不入池，提升者胜
    assert long_q in [b.text for b in out if isinstance(b, HeadingNode)]  # 未被卷入竞争降级


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


def test_split_attachments_guard_empty_main():
    """A1 文档开头守卫（issues6 P0 #1）：首段即「附件1」的模版类文档不拆，
    整件归主文（此前主文 0 blocks、整文件成 attach1）。"""
    content = [H("附件1：可行性研究报告", level=1), P("模版正文"), P("表格说明")]
    main, attach = split_attachments(content)
    assert attach == []
    assert [b.text for b in main] == ["附件1：可行性研究报告", "模版正文", "表格说明"]


def test_split_attachments_guard_only_blocks_first_boundary():
    """守卫只挡首个边界：其后出现的附件边界正常拆。"""
    content = [H("附件1：表A", level=1), P("A内容"), H("附件2：表B", level=1), P("B内容")]
    main, attach = split_attachments(content)
    assert len(attach) == 1 and attach[0][0].text == "附件2：表B"
    assert [b.text for b in main] == ["附件1：表A", "A内容"]


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
