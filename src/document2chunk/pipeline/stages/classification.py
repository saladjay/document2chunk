"""元素分类 Stage（多信号综合判定）。

信号（并行，非串级）：
1. 字号比值 vs 正文基准 → H1–H4 分值
2. 编号模式（一、/（一）/第X章/1.）→ 纯标题强信号；编号+正文混合弱信号
3. 独立成行（宽度 <65% + 同行无他元素 + 非页眉页脚区）→ 辅助

综合评分 ≥ 0.50 → heading；否则 paragraph。

设计变更（designs/007 R2 方案A）：原实现仅用字号单信号，中文公文编号标题
（字号同正文）被漏检 → 级联失败。改为多信号综合，覆盖编号/居中/独立行等场景。
"""

from __future__ import annotations

import re

from document2chunk.pipeline.base import PipelineContext
from document2chunk.pipeline.common import infer_heading_level_with_score
from document2chunk.pipeline.heading_scorer import (
    HeadingScoreAccumulator,
    extract_section_number,
    is_standalone_line,
    section_number_depth,
)

_SKIP_TYPES = {"table", "toc_title", "toc_entry", "list", "image"}

# 纯日期行（落款日期/时间戳）——不判 heading（2019.9.17 把"2019年9月17日"误判标题）
_DATE_SEP = r"[\-/．\.年]"   # 日期分隔符（- 转义避免 char range）
_PURE_DATE_RE = re.compile(
    rf"^\d{{4}}\s*{_DATE_SEP}\s*\d{{1,2}}\s*(?:{_DATE_SEP}\s*\d{{1,2}}\s*日?)?\s*$"
    r"|^\d{4}\s*年\s*\d{1,2}\s*月\s*(\d{1,2}\s*日)?\s*$"
)

# 多信号评分权重
_SCORE_SECTION_NUM_PURE = 0.65   # 纯标题（编号开头，无句号后正文）
_SCORE_SECTION_NUM_MIXED = 0.20  # 编号+正文混合（弱信号）
_SCORE_STANDALONE = 0.20         # 独立成行
_HEADING_THRESHOLD = 0.50        # 综合评分阈值

# 检测"句号后有正文"（编号开头但含正文 → 不是纯标题）
_BODY_AFTER_PUNCT_RE = re.compile(r"[。！？]\s*\S")


class ClassificationStage:
    """元素分类（多信号综合）。"""

    @property
    def name(self) -> str:
        return "classification"

    @property
    def is_global(self) -> bool:
        return False

    def process(self, elements: list[dict], ctx: PipelineContext) -> list[dict]:
        body_font = ctx.body_font or "Unknown"
        body_size = ctx.body_font_size or 12.0
        page_w = getattr(ctx, "page_width", 0) or 0
        page_h = getattr(ctx, "page_height", 0) or 0

        for elem in elements:
            if elem.get("type") in _SKIP_TYPES:
                continue

            style = elem.get("style", {})
            scorer = HeadingScoreAccumulator(elem)

            self._classify_pdf(
                elem, style, body_font, body_size, scorer,
                elements, page_w, page_h,
            )

            scorer.apply_to(elem)

        return elements

    # ── PDF（可编辑）：多信号综合 ──

    @staticmethod
    def _classify_pdf(
        elem: dict,
        style: dict,
        body_font: str,
        body_size: float,
        scorer: HeadingScoreAccumulator,
        all_elements: list[dict],
        page_width: float,
        page_height: float,
    ) -> None:
        font = style.get("font", "")
        size = style.get("size", 0)
        text = (elem.get("text") or "").strip()
        is_body = font == body_font and abs(size - body_size) <= 0.5

        # 纯日期/时间戳行（落款日期）→ 强制段落，不判标题
        if _PURE_DATE_RE.match(text):
            elem["type"] = "paragraph"
            elem["level"] = None
            scorer.skip("classification", "pure_date", note="纯日期行不判标题")
            return

        # 信号 1：字号比值（非正文才看）
        font_level = None
        if not is_body:
            font_level, score = infer_heading_level_with_score(size, body_size)
            if font_level is not None:
                scorer.add_score(
                    stage="classification",
                    rule=f"font_size_H{font_level}",
                    score=score,
                    action="assign",
                )

        # 信号 2：编号模式（按 depth 分层，同字号同字体 = 语义亲缘）
        sec_num = extract_section_number(text)
        sec_depth = section_number_depth(sec_num) if sec_num else 0
        has_body = bool(_BODY_AFTER_PUNCT_RE.search(text))

        if sec_num:
            if sec_depth == 1:
                # 章级（一、/第X章）→ 强 heading 信号（即使同字号）
                score = 0.20 if has_body else _SCORE_SECTION_NUM_PURE
                scorer.add_score(
                    stage="classification",
                    rule=f"section_number_d1{'_mixed' if has_body else ''}",
                    score=score,
                    action="boost",
                    note=f"{sec_num} depth=1",
                )
            elif sec_depth >= 2 and not is_body:
                # 子级（（一）/第X节）+ 字号不同于正文 → 弱 heading 信号
                score = 0.15 if has_body else 0.45
                scorer.add_score(
                    stage="classification",
                    rule=f"section_number_d{sec_depth}{'_mixed' if has_body else ''}",
                    score=score,
                    action="boost",
                    note=f"{sec_num} depth={sec_depth} (非正文字号)",
                )
            # depth >= 2 + is_body → 不加分（同字号同字体的子级编号 = 段落标记）

        # 信号 3：独立成行
        if page_width > 0 and page_height > 0:
            standalone = is_standalone_line(elem, all_elements, page_width, page_height)
            if standalone:
                scorer.add_score(
                    stage="classification",
                    rule="standalone_line",
                    score=_SCORE_STANDALONE,
                    action="boost",
                )

        # 综合判定
        if scorer.confidence >= _HEADING_THRESHOLD:
            # 决定 level：优先字号 → 编号 depth → 默认 1
            if font_level is not None:
                lvl = font_level
            elif sec_depth > 0:
                lvl = sec_depth
            else:
                lvl = 1
            elem["type"] = "title" if lvl == 1 else "heading"
            elem["level"] = lvl
        else:
            elem["type"] = "paragraph"
            elem["level"] = None
