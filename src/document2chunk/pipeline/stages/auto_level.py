"""自动层级分配 Stage。

通过多规则叠加评分，为非正文元素分配 heading level：
- 章节号正则 + 独立一行（0.35）
- 加粗 + 独立一行（0.30）
- 字号略大 + 独立一行（0.25）
- 字体不同 + 独立一行（0.20）
- 行距偏大 + 独立一行（0.15）

累计 confidence ≥ 0.50 才赋予 heading level。

source 感知：OCR 的 span flags 恒 0，``is_bold = bool(flags & 0x10)`` 恒为 False，
故 bold 规则自然不触发（满足 ocr-extractor spec §3「bold 失效降权」），无需分支。

全局 stage：需要跨页扫描 max_level 和平均行距。
"""

from __future__ import annotations

from document2chunk.pipeline.base import PipelineContext
from document2chunk.pipeline.heading_scorer import (
    HeadingScoreAccumulator,
    extract_section_number,
    is_standalone_line,
    section_number_depth,
)

# AutoLevel 规则评分
_SCORE_SECTION_NUM = 0.35  # 章节号正则 + 独立一行
_SCORE_BOLD = 0.30  # 加粗 + 独立一行
_SCORE_SIZE_NEAR = 0.25  # 字号略大 + 独立一行
_SCORE_FONT_DIFF = 0.20  # 字体不同 + 独立一行
_SCORE_LINE_GAP = 0.15  # 行距偏大 + 独立一行

# 置信度阈值
_CONFIDENCE_THRESHOLD = 0.50


class AutoLevelStage:
    """自动分配末级标题（多规则评分版）。

    - is_global = True（跨页运行）
    - 扫描所有 elements 找 max(level)
    - 对 level is None 或 confidence < 0.50 的元素，尝试多规则评分
    - 累计 confidence ≥ 0.50 → 赋予 heading level
    """

    @property
    def name(self) -> str:
        return "auto_level"

    @property
    def is_global(self) -> bool:
        return True

    def process(
        self,
        elements: list[dict],
        ctx: PipelineContext,
    ) -> list[dict]:
        body_font = ctx.body_font or "Unknown"
        body_size = ctx.body_font_size or 12.0

        # 扫描全局 max heading level
        max_level = 0
        for elem in elements:
            lvl = elem.get("level")
            if lvl is not None:
                max_level = max(max_level, lvl)

        ctx.max_heading_level = max_level

        if max_level == 0:
            # 文档没有任何标题层级，不自动分配
            return elements

        auto_level = max_level + 1
        auto_count = 0

        # 不参与自动分配的元素类型（已由其他 stage 明确标记）
        SKIP_TYPES = {"toc_entry", "toc_title", "table", "list", "image"}

        page_width = ctx.page_width
        page_height = ctx.page_height

        # 预计算平均行距
        avg_line_gap = self._calc_avg_line_gap(elements)

        # 按页分组元素（用于独立一行判定）
        pages_elements = self._group_by_page(elements)

        for elem in elements:
            scorer = HeadingScoreAccumulator(elem)
            elem_type = elem.get("type")

            # 跳过已明确标记的特殊类型
            if elem_type in SKIP_TYPES:
                scorer.skip(
                    "auto_level",
                    f"{elem_type}_skip",
                    note=f"{elem_type} 类型不参与 AutoLevel",
                )
                scorer.apply_to(elem)
                continue

            # 已有足够置信度的 heading → skip
            if elem.get("level") is not None and scorer.confidence >= _CONFIDENCE_THRESHOLD:
                scorer.skip(
                    "auto_level",
                    "high_confidence_skip",
                    note=f"已有 level={elem['level']}, confidence={scorer.confidence:.2f}",
                )
                scorer.apply_to(elem)
                continue

            style = elem.get("style", {})
            elem_font = style.get("font", "")
            elem_size = style.get("size", 0)
            elem_flags = style.get("flags", 0)
            size_near_body = abs(elem_size - body_size) <= 0.5
            size_slightly_larger = body_size < elem_size <= body_size * 1.05

            # 获取当前页的元素列表（用于独立一行判定）
            page_idx = elem.get("page_index", 0)
            page_elems = pages_elements.get(page_idx, elements)

            text = elem.get("text", "").strip()

            # ===== 规则判定 =====
            is_bold = bool(elem_flags & 0x10)  # bit 4 = bold（OCR flags 恒 0 → 恒 False）
            is_font_diff = elem_font != body_font
            is_section_num = (
                extract_section_number(text) is not None if text else False
            )
            is_standalone = (
                is_standalone_line(elem, page_elems, page_width, page_height)
                if page_width > 0 and page_height > 0
                else False
            )

            # 行距偏大判定
            is_large_gap = False
            if avg_line_gap > 0:
                is_large_gap = self._has_large_line_gap(elem, page_elems, avg_line_gap)

            # ===== 规则评分（仅独立一行时才检查各规则） =====
            if is_standalone:
                if is_section_num:
                    scorer.add_score(
                        "auto_level",
                        "section_number_standalone",
                        _SCORE_SECTION_NUM,
                        "boost",
                    )

                if is_bold:
                    scorer.add_score(
                        "auto_level",
                        "bold_standalone",
                        _SCORE_BOLD,
                        "boost",
                    )

                if size_slightly_larger:
                    scorer.add_score(
                        "auto_level",
                        "size_slightly_larger_standalone",
                        _SCORE_SIZE_NEAR,
                        "boost",
                    )

                if is_font_diff and not size_near_body:
                    scorer.add_score(
                        "auto_level",
                        "font_diff_standalone",
                        _SCORE_FONT_DIFF,
                        "boost",
                    )

                if is_large_gap:
                    scorer.add_score(
                        "auto_level",
                        "large_line_gap_standalone",
                        _SCORE_LINE_GAP,
                        "boost",
                    )
            else:
                # 非独立一行：记录各项 skip 原因
                if is_section_num:
                    scorer.skip(
                        "auto_level",
                        "section_num_not_standalone",
                        note="章节号但同行有其他元素",
                    )
                if is_bold:
                    scorer.skip(
                        "auto_level",
                        "bold_not_standalone",
                        note="加粗但同行有其他元素，不视为标题",
                    )

            # ===== 决策 =====
            if scorer.confidence >= _CONFIDENCE_THRESHOLD:
                # 确定是 heading
                # 如果有章节号 → 从 depth 推断 level
                sec_num = extract_section_number(text) if text else None
                if sec_num:
                    depth = section_number_depth(sec_num)
                    # depth=1 → level 2, depth=2 → level 3, ...
                    elem_level = depth + 1
                    scorer.add_score(
                        "auto_level",
                        "section_number_depth",
                        0.0,
                        "assign",
                        note=f"从章节号 depth={depth} 推断 level={elem_level}",
                    )
                else:
                    elem_level = auto_level

                elem["level"] = elem_level
                elem["type"] = "heading"
                auto_count += 1
            else:
                # 未达到阈值 → 保持原类型，记录 skip
                if is_standalone:
                    scorer.skip(
                        "auto_level",
                        "below_threshold",
                        note=f"confidence={scorer.confidence:.2f} < {_CONFIDENCE_THRESHOLD}",
                    )
                elif elem_type == "paragraph" and size_near_body:
                    scorer.skip(
                        "auto_level",
                        "body_size_paragraph_skip",
                        note="paragraph + size_near_body，保持正文",
                    )

            scorer.apply_to(elem)

        if auto_count > 0:
            ctx.stats["auto_level_count"] = auto_count
            ctx.stats["auto_level_value"] = auto_level

        return elements

    @staticmethod
    def _group_by_page(elements: list[dict]) -> dict[int, list[dict]]:
        """按 page_index 分组元素。"""
        pages: dict[int, list[dict]] = {}
        for elem in elements:
            page_idx = elem.get("page_index", 0)
            pages.setdefault(page_idx, []).append(elem)
        return pages

    @staticmethod
    def _calc_avg_line_gap(elements: list[dict]) -> float:
        """计算同页元素间的平均行距（y 坐标差）。

        按 page_index 分组后，每组内按 y 坐标排序，计算相邻元素 y0 的差值，取平均。
        """
        pages: dict[int, list[dict]] = {}
        for elem in elements:
            page_idx = elem.get("page_index", 0)
            pages.setdefault(page_idx, []).append(elem)

        all_gaps: list[float] = []
        for page_elems in pages.values():
            sorted_elems = sorted(
                [e for e in page_elems if e.get("bbox") and len(e["bbox"]) >= 4],
                key=lambda e: e["bbox"][1],
            )
            for i in range(1, len(sorted_elems)):
                prev_y1 = sorted_elems[i - 1]["bbox"][3]
                curr_y0 = sorted_elems[i]["bbox"][1]
                gap = curr_y0 - prev_y1
                if 0 < gap < 200:  # 排除异常值
                    all_gaps.append(gap)

        if not all_gaps:
            return 0.0
        return sum(all_gaps) / len(all_gaps)

    @staticmethod
    def _has_large_line_gap(
        elem: dict,
        page_elements: list[dict],
        avg_line_gap: float,
        threshold_ratio: float = 1.5,
    ) -> bool:
        """判断元素与上下相邻元素的行距是否偏大（默认 1.5 倍平均行距）。"""
        bbox = elem.get("bbox")
        if not bbox or len(bbox) < 4:
            return False

        elem_y0, elem_y1 = bbox[1], bbox[3]

        # 找同页中 y 坐标最近的前后元素
        prev_y1 = None
        next_y0 = None

        for other in page_elements:
            if other is elem:
                continue
            other_bbox = other.get("bbox")
            if not other_bbox or len(other_bbox) < 4:
                continue

            other_y0, other_y1 = other_bbox[1], other_bbox[3]
            other_center_y = (other_y0 + other_y1) / 2
            elem_center_y = (elem_y0 + elem_y1) / 2

            if other_center_y < elem_center_y:
                # 在上方
                if prev_y1 is None or other_y1 > prev_y1:
                    prev_y1 = other_y1
            elif other_center_y > elem_center_y:
                # 在下方
                if next_y0 is None or other_y0 < next_y0:
                    next_y0 = other_y0

        threshold = avg_line_gap * threshold_ratio

        # 上方行距偏大
        if prev_y1 is not None and (elem_y0 - prev_y1) > threshold:
            return True

        # 下方行距偏大
        if next_y0 is not None and (next_y0 - elem_y1) > threshold:
            return True

        return False
