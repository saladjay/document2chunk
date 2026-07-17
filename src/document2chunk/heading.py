"""共享标题定级 + 后处理模块（designs/005/007）。

编号样式 → 偏序（_STYLE_ORDER），实际层级由文档**栈序**推导（不写死）。
两路 extractor（edited-PDF + OCR）共用。
"""

from __future__ import annotations

import re
import statistics
from typing import Any, Dict, List, Optional, Tuple

from document2chunk.ir import BlockNode, DocumentMetadata, HeadingNode, ParagraphNode

# ── 编号正则 ──
_NUM = r"[一二三四五六七八九十百千零〇两]+"
RE_CHAPTER = re.compile(rf"^第{_NUM}章")
RE_SECTION = re.compile(rf"^第{_NUM}节")
RE_ARTICLE = re.compile(rf"^第{_NUM}条")
RE_CN_MAJOR = re.compile(rf"^{_NUM}、")
RE_CN_MINOR = re.compile(rf"^[（(]{_NUM}[）)]")
RE_DIGIT = re.compile(r"^(\d+[.、]|[（(]\d+[）)])")
RE_APPENDIX = re.compile(r"^(附\s*[表录件]|附\s*录|appendix)", re.IGNORECASE)

# 偏序（值小 = 高层级；非绝对 level，由栈序覆盖）
_STYLE_ORDER: Dict[str, int] = {
    "chapter": 0, "cn_major": 0,
    "section": 1, "cn_minor": 1,
    "article": 2,
    "digit": 3,
}

_HEIGHT_LEVELS = [(1.6, 1), (1.3, 2), (1.15, 3), (1.05, 4)]
DOC_TITLE_RATIO = 1.8
DOC_TITLE_EDITED_RATIO = 1.2  # edited-PDF 居中标题（字号含行距，阈值低）
_SENTENCE_END_MERGE = "。！？.!?"  # 多行标题合并：这些结尾表示完整（不含；：）
_MERGE_PAIR_LIMIT = 4

# 跨页 join：段落续接的句尾结束符（这些结尾不 join）
_SENTENCE_END_JOIN = "。！？.!?"  # 去掉了 ；;:： （分号/冒号后可继续）


# ════════════════════════════════════════
#  辅助函数
# ════════════════════════════════════════

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
    """检测块是否水平居中（edited-PDF 标题特征）。"""
    prov = getattr(node, "provenance", None)
    if not prov or not prov.bbox or prov.page_index is None:
        return False
    pw = (page_widths or {}).get(prov.page_index, 0)
    if pw <= 0:
        return False
    bb = prov.bbox
    center_x = (bb[0] + bb[2]) / 2
    return abs(center_x - pw / 2) < pw * 0.1  # ±10%


def _should_merge(t1: str, t2: str) -> bool:
    if style_of(t1) or style_of(t2):
        return False
    if not t1 or t1.rstrip()[-1:] in _SENTENCE_END_MERGE:
        return False
    return True


def _merge_headings(content: List[BlockNode]) -> List[BlockNode]:
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
                if isinstance(nxt, HeadingNode) and nxt.level == b.level and _should_merge(merged_text, nxt.text):
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


# ════════════════════════════════════════
#  ① calibrate — 自适应标题层级
# ════════════════════════════════════════

def calibrate(
    content: List[BlockNode],
    metadata: DocumentMetadata,
    *,
    use_height_fallback: bool = True,
    page_widths: Optional[Dict[int, float]] = None,
    _log: Optional[List[dict]] = None,
) -> List[BlockNode]:
    """文档级标题自适应重定级 + 多行合并 + 大标题 H1+metadata(C)。

    层级由文档**栈序**推导（先遇到的样式 = 高层级），_STYLE_ORDER 作偏序参考。
    大标题 → H1 + metadata.title；编号层级相对偏移。
    """
    def _log_add(**kw):
        if _log is not None:
            _log.append(kw)

    # 1. 正文基准高度
    para_hs = [_bbox_h(b) for b in content if isinstance(b, ParagraphNode)]
    para_hs = [h for h in para_hs if h > 0]
    body_h = statistics.mode(para_hs) if para_hs else 0.0

    # 2. 检测大标题
    doc_title_indices: List[int] = []  # content indices of doc-title HeadingNodes
    for i, b in enumerate(content):
        if not isinstance(b, HeadingNode):
            continue
        st = style_of(b.text)
        if st is not None:
            continue  # 有编号，不是大标题
        h = _bbox_h(b)
        ratio = (h / body_h) if body_h else 0.0
        centered = _is_centered(b, page_widths)
        is_doc = False
        if use_height_fallback and ratio >= DOC_TITLE_RATIO:
            is_doc = True
        elif not use_height_fallback and centered and ratio >= DOC_TITLE_EDITED_RATIO:
            is_doc = True
        if is_doc:
            doc_title_indices.append(i)
            _log_add(section="calibrate", block_id=b.id, text=(b.text or "")[:40],
                     detected="doc_title", action="→H1+metadata", reason=f"ratio={ratio:.1f} centered={centered}")

    has_doc_title = len(doc_title_indices) > 0
    level_offset = 1 if has_doc_title else 0

    # 最长大标题 → metadata.title + H1
    main_title_block: Optional[HeadingNode] = None
    doc_title_set = set(doc_title_indices)
    if has_doc_title:
        title_blocks = [content[i] for i in doc_title_indices]
        title_blocks.sort(key=lambda b: len(b.text or ""), reverse=True)
        main_title_block = title_blocks[0]
        metadata.title = main_title_block.text
        if len(title_blocks) > 1:
            metadata.custom["doc_titles"] = [b.text for b in title_blocks[1:]]

    # 3. 第二遍：自适应定级
    style_levels: Dict[str, int] = {}  # style → assigned level (per appendix section)
    next_style_level = 1 + level_offset

    new_content: List[BlockNode] = []
    prev_level = 0

    for i, b in enumerate(content):
        if not isinstance(b, HeadingNode):
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

        # 大标题处理（C: H1+metadata 或降级 Paragraph）
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

        # 编号标题：栈序自适应
        st = style_of(b.text)
        h = _bbox_h(b)
        ratio = (h / body_h) if body_h else 0.0

        if st:
            if st not in style_levels:
                style_levels[st] = next_style_level
                next_style_level += 1
            lvl = style_levels[st]
            _log_add(section="calibrate", block_id=b.id, text=(b.text or "")[:40],
                     detected=st, action=f"→H{lvl}", reason=f"栈序(level_offset={level_offset})")
        elif use_height_fallback:
            lvl = _height_level(ratio) + level_offset
        else:
            lvl = (b.level + level_offset) if has_doc_title else b.level

        lvl = max(1, min(lvl, 9))
        if prev_level and lvl > prev_level + 1:
            lvl = prev_level + 1
        prev_level = lvl
        b.level = lvl
        new_content.append(b)

    # 4. 多行标题合并
    new_content = _merge_headings(new_content)
    return new_content


# ════════════════════════════════════════
#  ② join_cross_page_paragraphs — 非紧邻按页边界查
# ════════════════════════════════════════

def join_cross_page_paragraphs(
    content: List[BlockNode],
    _log: Optional[List[dict]] = None,
) -> List[BlockNode]:
    """跨页段落续接：page N 末段 + page N+1 首段（不要求紧邻）。"""

    def _log_add(**kw):
        if _log is not None:
            _log.append(kw)

    # 1. 每页最后一个/第一个 ParagraphNode 索引
    page_last: Dict[int, int] = {}
    page_first: Dict[int, int] = {}
    for i, b in enumerate(content):
        if isinstance(b, ParagraphNode) and b.provenance and b.provenance.page_index is not None:
            pg = b.provenance.page_index
            page_last[pg] = i
            if pg not in page_first:
                page_first[pg] = i

    # 2. 收集 join 对
    join_pairs: List[Tuple[int, int]] = []
    for pg in sorted(page_last):
        nxt = pg + 1
        if nxt not in page_first:
            continue
        li, fi = page_last[pg], page_first[nxt]
        b1, b2 = content[li], content[fi]
        if _is_cross_page_continuation(b1, b2):
            join_pairs.append((li, fi))
            _log_add(section="join", page=pg, last_idx=li, next_page=nxt, first_idx=fi,
                     action="join", reason=f"跨页+无句号: '{(b1.text or '')[-15:]}'+'{(b2.text or '')[:15]}'")
        else:
            reason = "首段以句号结尾" if (b1.text or "").rstrip() and (b1.text or "").rstrip()[-1] in _SENTENCE_END_JOIN else "其他"
            _log_add(section="join", page=pg, last_idx=li, next_page=nxt, first_idx=fi,
                     action="skip", reason=reason)

    # 3. 从后往前执行（保持索引有效）
    removed: set = set()
    for li, fi in sorted(join_pairs, reverse=True):
        content[li].text = (content[li].text or "") + (content[fi].text or "")
        if hasattr(content[li], "runs") and hasattr(content[fi], "runs"):
            content[li].runs = list(content[li].runs) + list(content[fi].runs)
        removed.add(fi)
    return [b for i, b in enumerate(content) if i not in removed]


def _is_cross_page_continuation(b1: ParagraphNode, b2: ParagraphNode) -> bool:
    p1 = getattr(b1, "provenance", None)
    p2 = getattr(b2, "provenance", None)
    if not p1 or not p2:
        return False
    if p1.page_index is None or p2.page_index is None:
        return False
    if p2.page_index <= p1.page_index:
        return False
    t = (b1.text or "").rstrip()
    if t and t[-1] in _SENTENCE_END_JOIN:
        return False
    return True


# ════════════════════════════════════════
#  ③ filter_cross_page_noise — 跨页页眉/页脚过滤
# ════════════════════════════════════════

def filter_cross_page_noise(
    content: List[BlockNode], *, strip_ratio: float = 0.10, min_repeat: int = 2,
    _log: Optional[List[dict]] = None,
) -> List[BlockNode]:
    from collections import Counter, defaultdict

    def _log_add(**kw):
        if _log is not None:
            _log.append(kw)

    page_max_y: dict = defaultdict(float)
    for b in content:
        prov = getattr(b, "provenance", None)
        if prov and prov.bbox and prov.page_index is not None:
            page_max_y[prov.page_index] = max(page_max_y[prov.page_index], prov.bbox[3])

    strip_texts: Counter = Counter()
    for b in content:
        prov = getattr(b, "provenance", None)
        if not prov or not prov.bbox or prov.page_index is None:
            continue
        ph = page_max_y.get(prov.page_index, 0)
        if ph <= 0:
            continue
        y0, y1 = prov.bbox[1], prov.bbox[3]
        if y0 < ph * strip_ratio or y1 > ph * (1 - strip_ratio):
            t = (getattr(b, "text", "") or "").strip()
            if t:
                strip_texts[t] += 1

    noise = {t for t, c in strip_texts.items() if c >= min_repeat}
    if not noise:
        return content

    result = []
    for b in content:
        prov = getattr(b, "provenance", None)
        t = (getattr(b, "text", "") or "").strip()
        if t in noise and prov and prov.bbox and prov.page_index is not None:
            ph = page_max_y.get(prov.page_index, 0)
            if ph > 0:
                y0, y1 = prov.bbox[1], prov.bbox[3]
                if y0 < ph * strip_ratio or y1 > ph * (1 - strip_ratio):
                    _log_add(section="filter_noise", block_id=getattr(b, "id", ""), text=t[:30],
                             action="removed", reason="跨页重复(strip区)")
                    continue
        result.append(b)
    return result


# ════════════════════════════════════════
#  ④ split_attachments — 附件拆分
# ════════════════════════════════════════

def split_attachments(
    content: List[BlockNode],
    _log: Optional[List[dict]] = None,
) -> Tuple[List[BlockNode], List[List[BlockNode]]]:
    """按附表/附件/附录边界拆分 content → (正文, [附件1, 附件2, ...])。"""
    def _log_add(**kw):
        if _log is not None:
            _log.append(kw)

    segments: List[List[BlockNode]] = [[]]
    for b in content:
        if isinstance(b, HeadingNode) and RE_APPENDIX.match((b.text or "").strip()):
            segments.append([b])
            _log_add(section="split", split_at=getattr(b, "id", ""), heading=(b.text or "")[:30],
                     segment=f"attachment_{len(segments) - 1}")
        else:
            segments[-1].append(b)
    if len(segments) == 1:
        return segments[0], []
    return segments[0], segments[1:]
