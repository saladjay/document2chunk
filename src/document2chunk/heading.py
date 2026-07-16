"""共享标题定级模块（designs/005）。

编号模式 → 固定层级（参考 WebCrawler structure.py _STYLE_LEVEL）。
两路 extractor（edited-PDF + OCR）共用：
- edited-PDF：calibrate(use_height_fallback=False) → 仅编号覆盖，无编号保留 AutoLevel 原级。
- OCR：calibrate(use_height_fallback=True) → 编号 + 高度聚类 fallback + 大标题抽 metadata。

额外功能：
- 多行标题合并（Phase 1B）：相邻同 level 无编号标题、首段无句号结尾 → 合并。
"""

from __future__ import annotations

import re
import statistics
from typing import List, Optional

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

_STYLE_LEVEL = {"chapter": 1, "cn_major": 1, "section": 2, "cn_minor": 2, "article": 3, "digit": 4}

_HEIGHT_LEVELS = [(1.6, 1), (1.3, 2), (1.15, 3), (1.05, 4)]
DOC_TITLE_RATIO = 1.8
_SENTENCE_END = "。！？.!?；;:："
_MERGE_PAIR_LIMIT = 4  # 最多连续合并几个标题片段


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


def _should_merge(t1: str, t2: str) -> bool:
    """t1 + t2 是否该合并为一个多行标题。"""
    if style_of(t1) or style_of(t2):
        return False  # 编号标题是独立章节，不合并
    if not t1 or t1.rstrip()[-1:] in _SENTENCE_END:
        return False  # t1 以句号结尾 = 完整
    return True


def _merge_headings(content: List[BlockNode]) -> List[BlockNode]:
    """合并多行标题：相邻同 level 无编号标题、首段无句号 → 合并文本。"""
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
                b.text = merged_text  # mutate
            result.append(b)
            i = j
        else:
            result.append(b)
            i += 1
    return result


def calibrate(
    content: List[BlockNode],
    metadata: DocumentMetadata,
    *,
    use_height_fallback: bool = True,
) -> List[BlockNode]:
    """文档级标题重定级 + 多行合并 + 大标题抽 metadata。

    Args:
        use_height_fallback: True=OCR（无编号走高度聚类 + 大标题→metadata）;
                             False=edited-PDF（无编号保留原 AutoLevel 级，不做高度/大标题）。
    """
    # 1. 正文基准高度
    para_hs = [_bbox_h(b) for b in content if isinstance(b, ParagraphNode)]
    para_hs = [h for h in para_hs if h > 0]
    body_h = statistics.mode(para_hs) if para_hs else 0.0

    # 2. 编号覆盖 + 高度 fallback + 大标题识别
    new_content: List[BlockNode] = []
    doc_titles: List[str] = []
    prev_level = 0

    for b in content:
        if not isinstance(b, HeadingNode):
            new_content.append(b)
            continue

        # 附页/附件/附录 → 重置层级（新子文档，Phase 2G）
        if RE_APPENDIX.match((b.text or "").strip()):
            prev_level = 0
            b.level = 1
            new_content.append(b)
            continue

        st = style_of(b.text)
        h = _bbox_h(b)
        ratio = (h / body_h) if body_h else 0.0

        # 大标题（仅 OCR 模式 + 无编号 + 高比值）
        if use_height_fallback and st is None and ratio >= DOC_TITLE_RATIO:
            doc_titles.append(b.text)
            new_content.append(ParagraphNode(
                id=b.id, text=b.text, runs=b.runs,
                provenance=b.provenance, metadata=b.metadata,
            ))
            continue

        # 定级
        if st:
            lvl = _STYLE_LEVEL[st]
        elif use_height_fallback:
            lvl = _height_level(ratio)
        else:
            lvl = b.level  # edited-PDF：保留 AutoLevel 原级

        # 栈式单调
        if prev_level and lvl > prev_level + 1:
            lvl = prev_level + 1
        prev_level = lvl
        b.level = lvl
        new_content.append(b)

    # 3. 多行标题合并
    new_content = _merge_headings(new_content)

    # 4. 大标题 → metadata（仅 OCR 模式）
    if doc_titles:
        doc_titles.sort(key=len, reverse=True)
        metadata.title = doc_titles[0]
        if len(doc_titles) > 1:
            metadata.custom["doc_titles"] = doc_titles[1:]

    return new_content


# ── Phase 2E: 跨页段落续接 ──

_SENTENCE_END_CHARS = set("。！？.!?；;:：\n\r")


def join_cross_page_paragraphs(content: List[BlockNode]) -> List[BlockNode]:
    """跨页段落续接：page N 末段 + page N+1 首段，若首段无句号结尾 → 合并。

    典型场景："…因成片开发征"(pN 末) + "收土地的，不再…"(pN+1 首) → 合并成一句。
    """
    result: List[BlockNode] = []
    i = 0
    while i < len(content):
        b = content[i]
        if isinstance(b, ParagraphNode) and i + 1 < len(content):
            nxt = content[i + 1]
            if isinstance(nxt, ParagraphNode) and _is_cross_page_continuation(b, nxt):
                b.text = (b.text or "") + (nxt.text or "")
                if hasattr(b, "runs") and hasattr(nxt, "runs"):
                    b.runs = list(b.runs) + list(nxt.runs)
                i += 2
                result.append(b)
                continue
        result.append(b)
        i += 1
    return result


def _is_cross_page_continuation(b1: ParagraphNode, b2: ParagraphNode) -> bool:
    """b1(page N 末) + b2(page N+1 首) 是否是同一段落的跨页续接。"""
    p1 = getattr(b1, "provenance", None)
    p2 = getattr(b2, "provenance", None)
    if not p1 or not p2:
        return False
    if p1.page_index is None or p2.page_index is None:
        return False
    if p2.page_index <= p1.page_index:
        return False  # 同页或更早，不是跨页
    # 首段不以句号结尾 = 可能是跨页续接
    t = (b1.text or "").rstrip()
    if t and t[-1] in _SENTENCE_END_CHARS:
        return False
    return True
