"""全文档后处理核心（designs/009 统一 BlockNode 架构）。

两路（PDF / OCR）在 BlockNode 汇合后，统一经过此处。**每个关注点只有一个决策点**：

1. :func:`filter_noise`       —— 跨页页眉/页脚/页码移除（layout 证据 + 跨页重复 + 页码序列；
   绝不盲删顶/底 N%）。取代旧 LayoutFilter 噪声 + PageNumberDetection + filter_cross_page_noise。
2. :func:`merge_cross_page`   —— 跨页段落续接 + 多行标题合并。取代旧 join_cross_page_paragraphs
   + calibrate 的多行标题合并。
3. :func:`calibrate_levels`   —— 栈式自适应定级 + doc_title→H1 + appendix reset + toc 覆盖 +
   ParagraphNode doc_title 提升（修 R2）。取代旧 calibrate + AutoLevel + TOCAnalysis。
4. :func:`split_attachments`  —— 附件边界拆分。

公共入口 :func:`postprocess` 按上述顺序调用，返回 (main_content, attachment_segments)。

标题**类型**不由本模块决定（PDF 由 ClassificationStage、OCR 由 markdown ``#``），本模块
只做 level/noise/merge/split——唯一的类型变更是 doc_title 提升（OCR 无上游 Classification，
此为唯一入口）与 doc_title 降级去重。
"""

from __future__ import annotations

import re
import statistics
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

from document2chunk.ir import BlockNode, DocumentMetadata, HeadingNode, ParagraphNode, TableNode, TocEntry
from document2chunk.pipeline.stages.merge import _LIST_MARKER_RE

# ── 编号正则 ──
_NUM = r"[一二三四五六七八九十百千零〇两]+"
RE_CHAPTER = re.compile(rf"^第{_NUM}章")
RE_SECTION = re.compile(rf"^第{_NUM}节")
RE_ARTICLE = re.compile(rf"^第{_NUM}条")
RE_CN_MAJOR = re.compile(rf"^{_NUM}、")
RE_CN_MINOR = re.compile(rf"^[（(]{_NUM}[）)]")
RE_DIGIT = re.compile(r"^(\d+[.．、]|[（(]\d+[）)])")  # 含全角句点 ．（与 heading_scorer 对齐）
RE_APPENDIX = re.compile(r"^(附\s*[表录件]|附\s*录|appendix)", re.IGNORECASE)
# 附件标题（用于段落边界）：marker 后必须跟 数字/冒号/括号，排除「附件正文」这类正文
# heading 边界用宽 RE_APPENDIX（heading 可靠）；段落边界用此严正则
RE_APPENDIX_TITLE = re.compile(r"^附\s*[表录件]\s*[:：．\.、0-9（(]")

# 偏序（值小 = 高层级；非绝对 level，由栈序覆盖）
_STYLE_ORDER: Dict[str, int] = {
    "chapter": 0, "cn_major": 0,
    "section": 1, "cn_minor": 1,
    "article": 2,
    "digit": 3,
}

# doc_title 检测阈值
DOC_TITLE_RATIO = 1.8           # OCR：高度比 ≥ 此 → 大标题
DOC_TITLE_EDITED_RATIO = 1.2    # edited：居中 + 高度比 ≥ 此 → 大标题
_HEIGHT_LEVELS = [(1.6, 1), (1.3, 2), (1.15, 3), (1.05, 4)]

# 跨页 join / 多行标题：句尾结束符（这些结尾不续接）
_SENTENCE_END_MERGE = "。！？.!?"
_SENTENCE_END_JOIN = "。！？.!?"   # 去掉了 ；;:： （分号/冒号后可继续）
_MERGE_PAIR_LIMIT = 4

# filter_noise 参数
NON_BODY_LABELS = {"number", "header", "footer", "page_header", "page_footer", "page_number"}
_HEADER_FOOTER_BAND = (0.08, 0.92)   # 顶 8% / 底 8% 作为页眉页脚带
_REPEAT_MIN_PAGES = 3                # 跨页重复 ≥ 此页数才判噪声（防误删跨页章节标题）
_REPEAT_MIN_LEN = 10                 # 跨页重复文本最短长度（短文本走页码序列检测）
_PAGE_NUM_BAND = 0.70                # 页码 y 位置 ≥ max_y × 此（用户反馈 70%-100%）
_PAGE_NUM_WIDTH_RATIO = 0.5          # 页码宽度 < 同行最大宽 × 此
_PAGE_NUM_MIN_HITS = 3               # 页码序列至少 N 个匹配
_PAGE_NUM_RE = re.compile(
    r"^(?:\d+|\d+\s*/\s*\d+|第\s*\d+\s*页|[-·\s]*\d+[-·\s]*|page\s+\d+)$",
    re.IGNORECASE,
)


# ══════════════════════════════════════
#  辅助函数
# ══════════════════════════════════════

def style_of(text: str) -> Optional[str]:
    t = (text or "").strip()
    for pat, name in (
        (RE_CHAPTER, "chapter"), (RE_SECTION, "section"), (RE_ARTICLE, "article"),
        (RE_CN_MAJOR, "cn_major"), (RE_CN_MINOR, "cn_minor"), (RE_DIGIT, "digit"),
    ):
        if pat.match(t):
            return name
    return None


def _bbox_h(node) -> float:
    prov = getattr(node, "provenance", None)
    bb = getattr(prov, "bbox", None) if prov else None
    return (bb[3] - bb[1]) if bb and len(bb) >= 4 else 0.0


def _height_level(ratio: float) -> int:
    for thr, lvl in _HEIGHT_LEVELS:
        if ratio >= thr:
            return lvl
    return 5


def _is_centered(node, page_widths: Optional[Dict[int, float]] = None) -> bool:
    """块是否水平居中（edited-PDF 标题特征）。"""
    prov = getattr(node, "provenance", None)
    if not prov or not prov.bbox or prov.page_index is None:
        return False
    pw = (page_widths or {}).get(prov.page_index, 0)
    if pw <= 0:
        return False
    bb = prov.bbox
    center_x = (bb[0] + bb[2]) / 2
    return abs(center_x - pw / 2) < pw * 0.1  # ±10%


def _prov_page(node) -> Optional[int]:
    prov = getattr(node, "provenance", None)
    return getattr(prov, "page_index", None) if prov else None


# 政策文种关键词（标题几乎总含其一；版头机关名不含）——主标题排序 tiebreaker
_TITLE_KEYWORDS = (
    "办法", "规定", "通知", "条例", "意见", "决定", "方案", "制度",
    "公告", "命令", "细则", "目录", "清单", "标准", "规划", "纲要",
)


def _title_rank(b: BlockNode) -> tuple:
    """主标题排序键：(含文种关键词/《》, 文本长度) —— 大者优先。"""
    txt = (b.text or "")
    has_kw = any(kw in txt for kw in _TITLE_KEYWORDS) or ("《" in txt and "》" in txt)
    return (1 if has_kw else 0, len(txt.strip()))


def _prov_bbox(node) -> Optional[list]:
    prov = getattr(node, "provenance", None)
    return getattr(prov, "bbox", None) if prov else None


# ══════════════════════════════════════
#  ① filter_noise —— 跨页噪声 + 页码（三证据）
# ══════════════════════════════════════

def _normalize_repeat_text(text: str) -> str:
    """归一化跨页重复检测：数字 → #N，空白折叠。兜住"第 1 页"/"第 2 页"差异。"""
    t = re.sub(r"\d+", "#N", text or "")
    return re.sub(r"\s+", " ", t).strip()


def _find_increasing_runs(values: List[int]) -> List[List[int]]:
    """在排序后的整数列表里找单调递增（差 1 或 2，容忍首页缺失）的 run。返回 run 列表。"""
    if not values:
        return []
    uniq = sorted(set(values))
    runs: List[List[int]] = [[uniq[0]]]
    for v in uniq[1:]:
        last = runs[-1][-1]
        if 1 <= v - last <= 2:
            runs[-1].append(v)
        else:
            runs.append([v])
    return runs


def filter_noise(
    content: List[BlockNode],
    *,
    layout_data: Optional[list] = None,
    page_geometry: Optional[Dict[int, Tuple[float, float]]] = None,
    _log: Optional[List[dict]] = None,
) -> List[BlockNode]:
    """跨页页眉/页脚/页码移除。

    三证据分层（强→弱），**绝不盲删顶/底 N%**（R9 根因）：
    1. layout 强证据：版面框标了 header/footer/number → 中心点落入即移除。
    2. 跨页重复：顶/底带内文本（数字归一化）在 ≥3 页同一位置出现 → 移除。
    3. 页码序列：底部 + 同行较窄 + 纯数字/N/M，形成跨页递增序列 → 移除。
    """
    def _log_add(**kw):
        if _log is not None:
            _log.append(kw)

    page_geometry = page_geometry or {}

    # 每页 max_y（用于相对位置判定）
    page_max_y: Dict[int, float] = defaultdict(float)
    for b in content:
        bb = _prov_bbox(b)
        pg = _prov_page(b)
        if bb and len(bb) >= 4 and pg is not None:
            page_max_y[pg] = max(page_max_y[pg], bb[3])

    noise_ids: set = set()

    # ── 步骤 1：layout 强证据 ──
    if layout_data:
        try:
            from document2chunk.pipeline.stages.layout_filter import layout_boxes_for_page
        except Exception:
            layout_boxes_for_page = None
        if layout_boxes_for_page is not None:
            for b in content:
                if b.id in noise_ids:
                    continue
                pg = _prov_page(b)
                bb = _prov_bbox(b)
                if pg is None or not bb or len(bb) < 4:
                    continue
                geo = page_geometry.get(pg)
                if not geo:
                    continue
                pw, ph = geo
                cx = (bb[0] + bb[2]) / 2
                cy = (bb[1] + bb[3]) / 2
                expand = ph * 0.05
                for label, lbox in layout_boxes_for_page(layout_data, pg, pw, ph):
                    if label in NON_BODY_LABELS:
                        if (lbox[0] - expand <= cx <= lbox[2] + expand
                                and lbox[1] - expand <= cy <= lbox[3] + expand):
                            noise_ids.add(b.id)
                            _log_add(section="filter_noise", block_id=b.id,
                                     text=(getattr(b, "text", "") or "")[:30],
                                     action="removed", reason=f"layout:{label}")
                            break

    # ── 步骤 2：跨页重复（页眉/页脚，含页码嵌页脚）──
    band_hits: Dict[Tuple[str, str], List[Tuple[int, str]]] = defaultdict(list)
    for b in content:
        if b.id in noise_ids:
            continue
        pg = _prov_page(b)
        bb = _prov_bbox(b)
        if pg is None or not bb or len(bb) < 4:
            continue
        max_y = page_max_y.get(pg, 0)
        if max_y <= 0:
            continue
        text = (getattr(b, "text", "") or "").strip()
        if len(text) < _REPEAT_MIN_LEN:
            continue  # 短文本走步骤 3 页码序列
        y0, y1 = bb[1], bb[3]
        if y0 < max_y * _HEADER_FOOTER_BAND[0]:
            position = "top"
        elif y1 > max_y * _HEADER_FOOTER_BAND[1]:
            position = "bottom"
        else:
            continue
        norm = _normalize_repeat_text(text)
        band_hits[(norm, position)].append((pg, b.id))

    for (norm, position), hits in band_hits.items():
        if len({h[0] for h in hits}) >= _REPEAT_MIN_PAGES:
            for _, bid in hits:
                if bid not in noise_ids:
                    noise_ids.add(bid)
                    _log_add(section="filter_noise", block_id=bid, action="removed",
                             reason=f"repeat:{position}/{norm[:20]}")

    # ── 步骤 3：页码（issues4 判据：底部/顶部带 + 宽度远小于上方文本 + 页码正则）──
    # 页码 y 上方最接近的 bbox 宽度比页码大很多（页码窄），位置 70%-100% 或顶部 8%。
    geo_w = {pg: w for pg, (w, _h) in page_geometry.items()}
    pageno_ids: set = set()
    candidates: List[Tuple[int, str, int]] = []  # 序列备用（宽度判据未命中时）
    for b in content:
        if b.id in noise_ids:
            continue
        pg = _prov_page(b)
        bb = _prov_bbox(b)
        if pg is None or not bb or len(bb) < 4:
            continue
        max_y = page_max_y.get(pg, 0)
        if max_y <= 0:
            continue
        in_bottom = bb[3] >= max_y * _PAGE_NUM_BAND
        in_top = bb[1] < max_y * _HEADER_FOOTER_BAND[0]
        if not (in_bottom or in_top):
            continue
        text = (getattr(b, "text", "") or "").strip()
        if not _PAGE_NUM_RE.match(text):
            continue
        my_w = bb[2] - bb[0]
        # 上方最近文本块的宽度（页码上方最接近的 bbox，30% 页高内）
        above_w = 0.0
        for o in content:
            if o.id == b.id or _prov_page(o) != pg:
                continue
            obb = _prov_bbox(o)
            if not obb or len(obb) < 4:
                continue
            if obb[3] <= bb[1] and obb[1] >= bb[1] - max_y * 0.3:
                w = obb[2] - obb[0]
                if w > above_w:
                    above_w = w
        # 主判据：宽度 < 上方最大宽 × 50%（页码远窄于正文）→ 页码（无需序列）
        if above_w > 0 and my_w < above_w * _PAGE_NUM_WIDTH_RATIO:
            pageno_ids.add(b.id)
            _log_add(section="filter_noise", block_id=b.id, action="removed",
                     reason="page_number:width+band")
            continue
        # 极端底部兜底（y1≥95% + 窄 < 页宽 12%）
        pw = geo_w.get(pg, 0)
        if pw > 0 and bb[3] >= max_y * 0.95 and my_w < pw * 0.12:
            pageno_ids.add(b.id)
            _log_add(section="filter_noise", block_id=b.id, action="removed",
                     reason="page_number:extreme_bottom")
            continue
        m = re.search(r"\d+", text)
        if m:
            candidates.append((pg, b.id, int(m.group())))

    # 序列确认（宽度/极端底部未命中时，跨页递增补判）
    if candidates:
        by_page: Dict[int, int] = {}
        for pg, bid, num in candidates:
            by_page.setdefault(pg, num)
        for run in _find_increasing_runs(sorted(by_page.values())):
            if len(run) >= _PAGE_NUM_MIN_HITS:
                tpages = {p for p, n in by_page.items() if n in run}
                for pg, bid, num in candidates:
                    if pg in tpages:
                        pageno_ids.add(bid)
                        _log_add(section="filter_noise", block_id=bid, action="removed",
                                 reason="page_number:sequence")

    noise_ids |= pageno_ids
    return [b for b in content if b.id not in noise_ids]


# ══════════════════════════════════════
#  ② merge_cross_page —— 跨页段落续接 + 多行标题合并
# ══════════════════════════════════════

def _should_merge_headings(t1: str, t2: str) -> bool:
    """多行标题是否合并：两者都无编号 + 前者无句尾。"""
    if style_of(t1) or style_of(t2):
        return False
    if not t1 or t1.rstrip()[-1:] in _SENTENCE_END_MERGE:
        return False
    return True


def _merge_headings(content: List[BlockNode]) -> List[BlockNode]:
    """合并连续无编号多行标题（如文章标题拆两行）。"""
    result: List[BlockNode] = []
    i = 0
    while i < len(content):
        b = content[i]
        if isinstance(b, HeadingNode):
            merged_text = b.text
            j = i + 1
            count = 0
            while j < len(content) and count < _MERGE_PAIR_LIMIT:
                nxt = content[j]
                if isinstance(nxt, HeadingNode) and nxt.level == b.level and _should_merge_headings(merged_text, nxt.text):
                    merged_text += nxt.text
                    j += 1
                    count += 1
                else:
                    break
            if count > 0:
                b.text = merged_text
            result.append(b)
            i = j
        else:
            result.append(b)
            i += 1
    return result


def _is_cross_page_continuation(b1: ParagraphNode, b2: ParagraphNode) -> bool:
    p1, p2 = _prov_page(b1), _prov_page(b2)
    if p1 is None or p2 is None or p2 <= p1:
        return False
    t = (b1.text or "").rstrip()
    if t and t[-1] in _SENTENCE_END_JOIN:
        return False
    # R10：b2 以列表/编号标记开头 → 新段落/列表项，不 join
    t2 = (b2.text or "").strip()
    if _LIST_MARKER_RE.match(t2):
        return False
    return True


def merge_cross_page(
    content: List[BlockNode],
    *,
    _log: Optional[List[dict]] = None,
) -> List[BlockNode]:
    """跨页段落续接 + 多行标题合并（全文档）。"""
    def _log_add(**kw):
        if _log is not None:
            _log.append(kw)

    # 1. 段落续接：page N 末段 + page N+1 首段
    page_last: Dict[int, int] = {}
    page_first: Dict[int, int] = {}
    for i, b in enumerate(content):
        if isinstance(b, ParagraphNode):
            pg = _prov_page(b)
            if pg is not None:
                page_last[pg] = i
                if pg not in page_first:
                    page_first[pg] = i

    join_pairs: List[Tuple[int, int]] = []
    for pg in sorted(page_last):
        nxt = pg + 1
        if nxt not in page_first:
            continue
        li, fi = page_last[pg], page_first[nxt]
        if _is_cross_page_continuation(content[li], content[fi]):
            join_pairs.append((li, fi))
            _log_add(section="merge", page=pg, action="join",
                     reason=f"'{(content[li].text or '')[-12:]}'+'{(content[fi].text or '')[:12]}'")

    removed: set = set()
    for li, fi in sorted(join_pairs, reverse=True):
        content[li].text = (content[li].text or "") + (content[fi].text or "")
        if hasattr(content[li], "runs") and hasattr(content[fi], "runs"):
            content[li].runs = list(content[li].runs) + list(content[fi].runs)
        removed.add(fi)
    content = [b for i, b in enumerate(content) if i not in removed]

    # 2. 多行标题合并
    content = _merge_headings(content)
    return content


# ══════════════════════════════════════
#  ③ calibrate_levels —— 栈式定级 + doc_title + toc 覆盖
# ══════════════════════════════════════

def _promote_doc_title_paragraphs(
    content: List[BlockNode],
    body_h: float,
    *,
    page_widths: Optional[Dict[int, float]],
    use_height_fallback: bool,
) -> List[BlockNode]:
    """doc_title 提升（修 R2）：仅 OCR（use_height_fallback=True）。

    OCR 无上游 ClassificationStage，文档大标题常被服务标成 text → ParagraphNode，
    此处按高度比 ≥ DOC_TITLE_RATIO 提升。**edited-PDF 不走此路径**——ClassificationStage
    已用 font 信号判定标题，pre-scan 会把多个居中段落都误提升（实测在 HTML 版 PDF 上
    把正文段落误当大标题）。edited 的 doc_title 由 calibrate_levels 在已有 HeadingNode
    中检测（居中 + 高度比）。
    """
    if body_h <= 0 or not use_height_fallback:
        return content
    out: List[BlockNode] = []
    for b in content:
        if isinstance(b, ParagraphNode):
            txt = (b.text or "").strip()
            if txt and not style_of(txt) and not RE_APPENDIX.match(txt):
                h = _bbox_h(b)
                ratio = h / body_h if body_h else 0.0
                if ratio >= DOC_TITLE_RATIO:
                    out.append(HeadingNode(
                        id=b.id, level=1, text=txt,
                        runs=getattr(b, "runs", []),
                        provenance=b.provenance, metadata=getattr(b, "metadata", {}),
                    ))
                    continue
        out.append(b)
    return out


def _detect_doc_title_indices(
    content: List[BlockNode],
    body_h: float,
    page_widths: Optional[Dict[int, float]],
    use_height_fallback: bool,
) -> List[int]:
    """从已有 HeadingNode 检测 doc_title 候选索引。

    高度检测（OCR ratio≥1.8 / edited 居中+ratio≥1.2）；无候选时取首个无编号
    L1/L2 + len≥8（窄 fallback）。跨页重复文本（页面家具）排除。
    """
    heading_text_counts: "Counter[str]" = Counter(
        (b.text or "").strip() for b in content if isinstance(b, HeadingNode)
    )
    indices: List[int] = []
    for i, b in enumerate(content):
        if not isinstance(b, HeadingNode):
            continue
        txt = (b.text or "").strip()
        if style_of(txt) is not None or RE_APPENDIX.match(txt):
            continue
        if heading_text_counts[txt] > 1:
            continue
        h = _bbox_h(b)
        ratio = (h / body_h) if body_h else 0.0
        centered = _is_centered(b, page_widths)
        if (use_height_fallback and ratio >= DOC_TITLE_RATIO) or (
            not use_height_fallback and centered and ratio >= DOC_TITLE_EDITED_RATIO
        ):
            indices.append(i)
    if not indices:
        for i, b in enumerate(content):
            if not isinstance(b, HeadingNode):
                continue
            txt = (b.text or "").strip()
            if RE_APPENDIX.match(txt) or style_of(txt) is not None:
                continue
            if heading_text_counts[txt] > 1:
                continue
            if b.level <= 2 and len(txt) >= 8:
                indices.append(i)
                break
    return indices


def _clean_toc_text(text: str) -> str:
    """清理 TOC 条目文本：去点线引导符 + 尾部页码（迁自 toc_analysis）。"""
    parts = re.split(r"\.{2,}|…+|·{2,}", text or "")
    clean = parts[0].strip()
    clean = re.sub(r"\s*\d+\s*$", "", clean)
    return clean.strip()


def _build_toc_mapping(toc_entries: Optional[List[TocEntry]]) -> Dict[str, int]:
    """从 TocEntry 构建 {heading_text: level} 映射（迁自 TOCAnalysisStage）。"""
    if not toc_entries:
        return {}
    # 优先用 TocEntry 自带的 level（已校准则直接用）
    has_level = [e for e in toc_entries if e.level is not None and e.text]
    if has_level:
        return {e.text: e.level for e in has_level}
    # 否则用编号 depth 推断（level_offset=1，文档主标题占 level 1）
    # key 用完整文本（含编号），与正文标题文本对齐（正文标题保留编号）
    mapping: Dict[str, int] = {}
    for e in toc_entries:
        text = _clean_toc_text(e.text)
        if not text:
            continue
        sec_num = _extract_section_number(text)
        depth = _section_number_depth(sec_num) if sec_num else 0
        if depth > 0:
            mapping[text] = depth + 1
    return mapping


def _extract_section_number(text: str) -> Optional[str]:
    if not text:
        return None
    text = text.strip()
    m = re.match(
        r"^((\d+(?:\.\d+)*)|(第[一二三四五六七八九十百千]+[章节条篇部])|([一二三四五六七八九十]+、)|([（(][一二三四五六七八九十]+[）)]))",
        text,
    )
    return m.group(1).strip() if m else None


def _section_number_depth(section_number: str) -> int:
    if not section_number:
        return 0
    if re.match(r"^\d+(\.\d+)*$", section_number):
        return len(section_number.split("."))
    m = re.match(r"^第[一二三四五六七八九十百千]+([章节条篇部])$", section_number)
    if m:
        return 1 if m.group(1) == "章" else 2
    if re.match(r"^[一二三四五六七八九十]+、$", section_number):
        return 1
    if re.match(r"^[（(][一二三四五六七八九十]+[）)]$", section_number):
        return 2
    return 1


def _match_toc_level(text: str, toc_map: Dict[str, int]) -> Optional[int]:
    """正文标题文本 → toc 映射 level（精确 / 前缀≥4 / 去尾标点）。"""
    if not toc_map:
        return None
    if text in toc_map:
        return toc_map[text]
    for toc_text, lvl in toc_map.items():
        if len(toc_text) >= 4 and text.startswith(toc_text):
            return lvl
    cleaned = re.sub(r"[。，,.\s]+$", "", text)
    if cleaned != text and len(cleaned) >= 4 and cleaned in toc_map:
        return toc_map[cleaned]
    return None


def calibrate_levels(
    content: List[BlockNode],
    metadata: DocumentMetadata,
    *,
    page_widths: Optional[Dict[int, float]] = None,
    toc_entries: Optional[List[TocEntry]] = None,
    use_height_fallback: bool = True,
    _log: Optional[List[dict]] = None,
) -> List[BlockNode]:
    """文档级标题自适应定级 + doc_title→H1 + appendix reset + toc 覆盖。

    - 先提升 doc_title ParagraphNode（R2）
    - 检测大标题（最长→metadata.title，其余→custom 降级 Paragraph）
    - 栈序自适应定级（首次出现的编号样式 = 高层级）
    - toc_entries 精确/前缀匹配覆盖栈序定级
    - 完成后回写 TocEntry.level（消除 assemble 双重消费）
    """
    def _log_add(**kw):
        if _log is not None:
            _log.append(kw)

    # 0. 正文基准高度
    para_hs = [_bbox_h(b) for b in content if isinstance(b, ParagraphNode)]
    para_hs = [h for h in para_hs if h > 0]
    body_h = statistics.mode(para_hs) if para_hs else 0.0

    # 0b. doc_title 检测（已有 HeadingNode）
    toc_map = _build_toc_mapping(toc_entries)
    doc_title_indices = _detect_doc_title_indices(
        content, body_h, page_widths, use_height_fallback
    )
    for i in doc_title_indices:
        b = content[i]
        _log_add(section="calibrate", block_id=b.id, text=(b.text or "")[:40],
                 detected="doc_title", action="→候选", reason="heading 检测")

    # 0c. OCR 兜底（R2）：若没有任何 heading 级 doc_title，才提升 ParagraphNode 再检测。
    # 仅 OCR 服务未把标题标成 `#`（留作段落）时触发；避免标题已是 HeadingNode 时
    # 把多行正文段落（bbox 高、ratio≥1.8）误提升为竞争性 doc_title。
    if not doc_title_indices and use_height_fallback:
        content = _promote_doc_title_paragraphs(
            content, body_h, page_widths=page_widths, use_height_fallback=True
        )
        doc_title_indices = _detect_doc_title_indices(
            content, body_h, page_widths, use_height_fallback
        )
        for i in doc_title_indices:
            b = content[i]
            _log_add(section="calibrate", block_id=b.id, text=(b.text or "")[:40],
                     detected="doc_title(promoted)", action="→候选", reason="R2 段落提升")

    has_doc_title = len(doc_title_indices) > 0
    doc_title_set: set = set(doc_title_indices)
    level_offset = 1 if has_doc_title else 0

    # 最长大标题 → metadata.title + H1；其余 → custom 降级 Paragraph
    main_title_block: Optional[HeadingNode] = None
    if has_doc_title:
        title_blocks = [content[i] for i in doc_title_indices]
        # 主标题优先级：含政策文种关键词（办法/通知/规定/…）或《》者 > 同长度无关键词者
        # （版头机关名与真标题常同长度，关键词区分：真标题含文种，版头不含）
        title_blocks.sort(key=_title_rank, reverse=True)
        main_title_block = title_blocks[0]
        metadata.title = main_title_block.text
        if len(title_blocks) > 1:
            metadata.custom["doc_titles"] = [b.text for b in title_blocks[1:]]

    # 2. 第二遍：自适应定级
    style_levels: Dict[str, int] = {}
    next_style_level = 1 + level_offset
    new_content: List[BlockNode] = []
    prev_level = 0

    for i, b in enumerate(content):
        if not isinstance(b, HeadingNode):
            # 附件边界为段落时（body-font 编号「附件1.xxx」未判 heading）也重置栈，
            # 使附件内标题层级独立于正文（与 split_attachments 边界一致）
            if RE_APPENDIX_TITLE.match((getattr(b, "text", "") or "").strip()):
                prev_level = 0
                style_levels = {}
                next_style_level = 1
            new_content.append(b)
            continue

        # 附页/附件/附录 → 重置
        if RE_APPENDIX.match((b.text or "").strip()):
            prev_level = 0
            style_levels = {}
            next_style_level = 1
            b.level = 1
            _log_add(section="calibrate", block_id=b.id, text=(b.text or "")[:40],
                     detected="appendix", action="→H1+reset", reason="附表/附件/附录")
            new_content.append(b)
            continue

        # 大标题处理
        if i in doc_title_set:
            if b is main_title_block:
                b.level = 1
                prev_level = 1
                new_content.append(b)
            else:
                new_content.append(ParagraphNode(
                    id=b.id, text=b.text, runs=b.runs,
                    provenance=b.provenance, metadata=b.metadata,
                ))
            continue

        txt = (b.text or "").strip()
        st = style_of(txt)

        # toc 覆盖（精确/前缀，优先于栈序）
        toc_lvl = _match_toc_level(txt, toc_map)
        if toc_lvl is not None:
            lvl = toc_lvl
            _log_add(section="calibrate", block_id=b.id, text=txt[:40],
                     detected="toc", action=f"→H{lvl}", reason="toc 映射覆盖")
        elif st:
            if st not in style_levels:
                style_levels[st] = next_style_level
                next_style_level += 1
            lvl = style_levels[st]
            _log_add(section="calibrate", block_id=b.id, text=txt[:40],
                     detected=st, action=f"→H{lvl}", reason=f"栈序(offset={level_offset})")
        else:
            h = _bbox_h(b)
            ratio = (h / body_h) if body_h else 0.0
            lvl = (_height_level(ratio) + level_offset) if use_height_fallback else (
                (b.level + level_offset) if has_doc_title else b.level
            )

        lvl = max(1, min(lvl, 9))
        if prev_level and lvl > prev_level + 1:
            lvl = prev_level + 1
        prev_level = lvl
        b.level = lvl
        new_content.append(b)

    # 3. 回写 TocEntry.level（消除 assemble 双重消费）
    if toc_entries and toc_map:
        for e in toc_entries:
            if e.level is None:
                cleaned = _clean_toc_text(e.text)
                lvl = _match_toc_level(cleaned, toc_map)
                if lvl is not None:
                    e.level = lvl

    return new_content


# ══════════════════════════════════════
#  ④ split_attachments —— 附件拆分
# ══════════════════════════════════════

def split_attachments(
    content: List[BlockNode],
    *,
    page_geometry: Optional[Dict[int, Tuple[float, float]]] = None,
    _log: Optional[List[dict]] = None,
) -> Tuple[List[BlockNode], List[List[BlockNode]]]:
    """按附表/附件/附录边界拆分 content → (正文, [附件1, 附件2, ...])。

    策略：**所有附件都拆成独立段**（用户要求）。边界 = 块文本起首匹配附件正则。
    - heading 用宽 RE_APPENDIX（可靠）；段落用严 RE_APPENDIX_TITLE（附件+数字/冒号，
      排除「附件正文」）。
    - ④ 同一块含 >1 个附件标记 = 引用清单（如「附件1：A 附件2：B」），不切。
    - page_geometry 暂留参数（page-top 曾作信号，但会漏掉 edited-PDF 页中附件，
      已撤为非硬门；位置/实质内容判据待数据支撑后再加，见 designs/010）。
    """
    def _log_add(**kw):
        if _log is not None:
            _log.append(kw)

    segments: List[List[BlockNode]] = [[]]
    for b in content:
        txt = (getattr(b, "text", "") or "").strip()
        if not txt:
            segments[-1].append(b)
            continue
        regex_ok = (
            (isinstance(b, HeadingNode) and bool(RE_APPENDIX.match(txt)))
            or (not isinstance(b, HeadingNode) and bool(RE_APPENDIX_TITLE.match(txt)))
        )
        # ④ 引用清单：一块里 >1 个附件标记 → 不切
        multi_ref = len(re.findall(r"附\s*[件表录]\s*[:：．\.、0-9（(]", txt)) > 1
        if regex_ok and not multi_ref:
            segments.append([b])
            _log_add(section="split", split_at=getattr(b, "id", ""),
                     heading=txt[:30], segment=f"attachment_{len(segments) - 1}")
        else:
            segments[-1].append(b)
    if len(segments) == 1:
        return segments[0], []
    return segments[0], segments[1:]


# ══════════════════════════════════════
#  公共入口
# ══════════════════════════════════════

def postprocess(
    blocks: List[BlockNode],
    metadata: DocumentMetadata,
    *,
    toc_entries: Optional[List[TocEntry]] = None,
    page_geometry: Optional[Dict[int, Tuple[float, float]]] = None,
    page_widths: Optional[Dict[int, float]] = None,
    layout_data: Optional[list] = None,
    use_height_fallback: bool = True,
    _log: Optional[List[dict]] = None,
) -> Tuple[List[BlockNode], List[List[BlockNode]]]:
    """两路共用的全文档后处理入口。

    顺序：filter_noise → merge_cross_page → calibrate_levels → split_attachments。
    page_widths 缺省时从 page_geometry 推导（取每页 width）。
    """
    if page_widths is None and page_geometry:
        page_widths = {pg: w for pg, (w, _h) in page_geometry.items()}

    blocks = filter_noise(blocks, layout_data=layout_data, page_geometry=page_geometry, _log=_log)
    blocks = merge_cross_page(blocks, _log=_log)
    blocks = calibrate_levels(
        blocks, metadata,
        page_widths=page_widths, toc_entries=toc_entries,
        use_height_fallback=use_height_fallback, _log=_log,
    )
    main_content, attach_segments = split_attachments(blocks, page_geometry=page_geometry, _log=_log)
    # 多页重复表头合并（按段：主文/各附件分别合并，避免跨段误并）
    main_content = merge_split_tables(main_content, _log=_log)
    attach_segments = [merge_split_tables(s, _log=_log) for s in attach_segments]
    return main_content, attach_segments


def _first_row_texts(t: TableNode) -> Tuple[str, ...]:
    """表格首行各单元格文本（表头指纹）。"""
    rows = getattr(t, "rows", None) or []
    if not rows:
        return ()
    cells = getattr(rows[0], "cells", None) or []
    texts: List[str] = []
    for c in cells:
        blocks = getattr(c, "blocks", []) or []
        texts.append("".join(getattr(b, "text", "") or "" for b in blocks).strip())
    return tuple(texts)


def merge_split_tables(
    content: List[BlockNode],
    *,
    _log: Optional[List[dict]] = None,
) -> List[BlockNode]:
    """合并连续的、首行表头相同的表格（跨页重复表头，2023.9.13）。

    长表跨页时每页重复表头 → 多个 TableNode 表头一致。合并：首表保留表头，
    后续表跳过各自表头（rows[1:]）追加为数据行。
    """
    def _log_add(**kw):
        if _log is not None:
            _log.append(kw)

    out: List[BlockNode] = []
    i = 0
    while i < len(content):
        b = content[i]
        if not isinstance(b, TableNode):
            out.append(b)
            i += 1
            continue
        hdr = _first_row_texts(b)
        if hdr:  # 有表头才尝试合并
            merged_rows = list(b.rows)
            j = i + 1
            while (
                j < len(content)
                and isinstance(content[j], TableNode)
                and _first_row_texts(content[j]) == hdr
            ):
                merged_rows.extend(content[j].rows[1:])  # 跳过重复表头
                j += 1
            if j > i + 1:
                b.rows = merged_rows
                _log_add(section="merge_tables", block_id=getattr(b, "id", ""),
                         action="merged", reason=f"合并 {j - i} 张同表头表，共 {len(merged_rows)} 行")
            out.append(b)
            i = j
        else:
            out.append(b)
            i += 1
    return out


__all__ = [
    "filter_noise",
    "merge_cross_page",
    "calibrate_levels",
    "split_attachments",
    "postprocess",
    "style_of",
]
