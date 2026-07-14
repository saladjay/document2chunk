"""元素分类 Stage。

根据正文基准 (body_font, body_font_size) 对每个 element 判定类型：
- paragraph：字体和字号都匹配正文基准
- heading / title：字号大于正文，按比值推断层级 H1–H4

同时写入 heading_confidence 初始评分和 heading_level_conf_history。

source 感知（ocr-extractor spec §3）：OCR 的 font 恒为 "OCR"、字号为估算值，
font-ratio 不可靠 → 走 ``ctx.source_type == "ocr"`` 分支：版面 ``title`` 标签
作主信号 → heading；其余 → paragraph。AutoLevel 的 bold 规则因 OCR flags 恒 0
自然失效，无需在此处理。
"""

from __future__ import annotations

from document2chunk.pipeline.base import PipelineContext
from document2chunk.pipeline.common import infer_heading_level_with_score
from document2chunk.pipeline.heading_scorer import HeadingScoreAccumulator

# 已明确类型、不需重新分类的元素
_SKIP_TYPES = {"table", "toc_title", "toc_entry", "list", "image"}

# OCR 版面 title 标签置信度（主信号）
_OCR_TITLE_SCORE = 0.50


class ClassificationStage:
    """元素分类。

    - is_global = False（逐页运行）
    - 读取 ctx.body_font, ctx.body_font_size, ctx.source_type
    - 设置每个 element 的 type、level、heading_confidence、heading_level_conf_history
    """

    @property
    def name(self) -> str:
        return "classification"

    @property
    def is_global(self) -> bool:
        return False

    def process(
        self,
        elements: list[dict],
        ctx: PipelineContext,
    ) -> list[dict]:
        body_font = ctx.body_font or "Unknown"
        body_size = ctx.body_font_size or 12.0
        is_ocr = ctx.source_type == "ocr"

        for elem in elements:
            # 跳过已有明确类型的元素
            if elem.get("type") in _SKIP_TYPES:
                continue

            style = elem.get("style", {})
            scorer = HeadingScoreAccumulator(elem)

            if is_ocr:
                self._classify_ocr(elem, style, scorer)
            else:
                self._classify_pdf(elem, style, body_font, body_size, scorer)

            scorer.apply_to(elem)

        return elements

    @staticmethod
    def _classify_pdf(
        elem: dict,
        style: dict,
        body_font: str,
        body_size: float,
        scorer: HeadingScoreAccumulator,
    ) -> None:
        """PDF（可编辑）：字号比值判定（H1–H4 阈值不变）。"""
        font = style.get("font", "")
        size = style.get("size", 0)

        is_body = font == body_font and abs(size - body_size) <= 0.5

        if is_body:
            elem["type"] = "paragraph"
            elem["level"] = None
            # 正文不写 confidence（保持 0）
        else:
            level, score = infer_heading_level_with_score(size, body_size)
            if level is not None:
                elem["type"] = "title" if level == 1 else "heading"
                elem["level"] = level
                scorer.add_score(
                    stage="classification",
                    rule=f"infer_heading_level_H{level}",
                    score=score,
                    action="assign",
                )
            else:
                # 非正文但字号不大于正文 → 暂标 paragraph，由 AutoLevel 后处理
                elem["type"] = "paragraph"
                elem["level"] = None

    @staticmethod
    def _classify_ocr(
        elem: dict,
        style: dict,
        scorer: HeadingScoreAccumulator,
    ) -> None:
        """OCR：版面 title 标签主信号 → heading；其余 → paragraph。

        level 留空（None），交由 AutoLevel/toc_analysis 赋级；映射层对 None
        级 heading 做兜底（默认 1）。OCR 字号估算噪声大，不据此判标题。
        """
        layout_label = style.get("layout_label")

        if layout_label == "title":
            elem["type"] = "heading"
            elem["level"] = None
            scorer.add_score(
                stage="classification",
                rule="ocr_layout_title",
                score=_OCR_TITLE_SCORE,
                action="assign",
                note="版面 title 标签（主信号）",
            )
        else:
            elem["type"] = "paragraph"
            elem["level"] = None
