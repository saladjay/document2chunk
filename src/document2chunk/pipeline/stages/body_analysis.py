"""正文基准分析 Stage。

统计全文 (font, size) 字符数，确定正文基准字体和字号。
全局 stage：跨所有页收集数据后计算一次。

source 感知：OCR 的 span 携带估算字号（bbox 高 × 72/DPI）、font="OCR"，
本 stage 的 (font,size) 众数逻辑同样适用（designs/003 §4）。
"""

from __future__ import annotations

from collections import defaultdict

from document2chunk.pipeline.base import PipelineContext
from document2chunk.pipeline.common import normalize_font_size


class BodyAnalysisStage:
    """正文基准分析。

    - is_global = True（跨页运行）
    - 透传 elements（不修改）
    - 写入 ctx.body_font, ctx.body_font_size
    """

    @property
    def name(self) -> str:
        return "body_analysis"

    @property
    def is_global(self) -> bool:
        return True

    def process(
        self,
        elements: list[dict],
        ctx: PipelineContext,
    ) -> list[dict]:
        # 统计 (font, size) → 字符数
        style_counts: dict[tuple[str, float], int] = defaultdict(int)

        for elem in elements:
            for span in elem.get("spans", []):
                text = span.get("text", "").strip()
                if text:
                    key = (
                        span.get("font", ""),
                        normalize_font_size(span.get("size", 0)),
                    )
                    style_counts[key] += len(text)

        # 合并到 ctx 的累计统计（支持多次调用）
        for key, count in style_counts.items():
            ctx.style_char_counts[key] = ctx.style_char_counts.get(key, 0) + count

        # 计算正文基准
        if ctx.style_char_counts:
            best = max(ctx.style_char_counts.items(), key=lambda x: x[1])
            ctx.body_font = best[0][0]
            ctx.body_font_size = best[0][1]
        else:
            ctx.body_font = "Unknown"
            ctx.body_font_size = 12.0

        return elements
