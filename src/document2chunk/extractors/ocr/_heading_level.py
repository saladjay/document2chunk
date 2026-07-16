"""OCR 文档级标题定级（designs/004）。

OCR 按页独立给 markdown `#` 层级，跨页不一致。本模块在所有页块汇总后做一次**文档级重定级**：
- 编号模式优先（`一、`→H1、`（一）`→H2、`第X章`→H1…，参考 WebCrawler structure.py）。
- 无编号 fallback：bbox 高度 / 正文基准 比值聚类（参考 edited-pdf BodyAnalysis）。
- 文档大标题（无编号 + 高比值）→ metadata.title / metadata.custom，降级为段落。
- 丢弃 markdown `#` 的 level（噪声），但其"是标题"信号已在建块时体现。
"""

from __future__ import annotations

import re
import statistics
from typing import List, Optional

from document2chunk.ir import BlockNode, DocumentMetadata, HeadingNode, ParagraphNode

_NUM = r"[一二三四五六七八九十百千零〇两]+"
RE_CHAPTER = re.compile(rf"^第{_NUM}章")
RE_SECTION = re.compile(rf"^第{_NUM}节")
RE_ARTICLE = re.compile(rf"^第{_NUM}条")
RE_CN_MAJOR = re.compile(rf"^{_NUM}、")
RE_CN_MINOR = re.compile(rf"^[（(]{_NUM}[）)]")
RE_DIGIT = re.compile(r"^(\d+[.、]|[（(]\d+[）)])")

# 编号样式 → 层级（structure.py _STYLE_LEVEL；OCR 无 bold，cn_minor 不要求粗体）
_STYLE_LEVEL = {
    "chapter": 1, "cn_major": 1,
    "section": 2, "cn_minor": 2,
    "article": 3, "digit": 4,
}

# 无编号高度聚类阈值（ratio = h / body_h，参考 edited-pdf H1≥1.6× / H2≥1.3× / H3≥1.15× / H4≥1.05×）
_HEIGHT_LEVELS = [(1.6, 1), (1.3, 2), (1.15, 3), (1.05, 4)]

# 无编号且 ratio ≥ 此 → 文档大标题（进 metadata，不进 heading 池）
DOC_TITLE_RATIO = 1.8


def _style_of(text: str) -> Optional[str]:
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
    return 5  # 略大于正文


def calibrate(content: List[BlockNode], metadata: DocumentMetadata) -> List[BlockNode]:
    """文档级重定级 HeadingNode + 抽文档大标题到 metadata。返回新 content 列表。"""
    # 1. 正文基准高度（段落 bbox 高度众数）
    para_hs = [_bbox_h(b) for b in content if isinstance(b, ParagraphNode)]
    para_hs = [h for h in para_hs if h > 0]
    body_h = statistics.mode(para_hs) if para_hs else 0.0

    new_content: List[BlockNode] = []
    doc_titles: List[str] = []
    prev_level = 0  # 栈式单调：防止无依据的层级跳跃

    for b in content:
        if not isinstance(b, HeadingNode):
            new_content.append(b)
            continue

        st = _style_of(b.text)
        h = _bbox_h(b)
        ratio = (h / body_h) if body_h else 0.0

        # 文档大标题（无编号 + 高比值）→ metadata，降级段落
        if st is None and ratio >= DOC_TITLE_RATIO:
            doc_titles.append(b.text)
            new_content.append(ParagraphNode(
                id=b.id, text=b.text, runs=b.runs,
                provenance=b.provenance, metadata=b.metadata,
            ))
            continue

        # 定级：编号优先，无编号走高度聚类
        lvl = _STYLE_LEVEL[st] if st else _height_level(ratio)
        # 单调：不允许一次跳超过 +1（除非从 0 起）
        if prev_level and lvl > prev_level + 1:
            lvl = prev_level + 1
        prev_level = lvl
        b.level = lvl
        new_content.append(b)

    # 大标题 → metadata（最长者=title，其余=custom，不丢弃）
    if doc_titles:
        doc_titles.sort(key=len, reverse=True)
        metadata.title = doc_titles[0]
        if len(doc_titles) > 1:
            metadata.custom["doc_titles"] = doc_titles[1:]

    return new_content
