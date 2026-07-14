"""页码检测 Stage。

检测页面底部的页码元素，并将其标记为特殊类型（page_number）。

全局 stage：跨页统计，当多数页面（>=70%）的底部元素匹配某个正则时，
才认为该正则有效，并将所有匹配的底部元素标记为 page_number。
"""

from __future__ import annotations

import re

from document2chunk.pipeline.base import PipelineContext
from document2chunk.pipeline.config import (
    PAGE_NUMBER_PATTERNS,
    PAGE_NUMBER_THRESHOLD_RATIO,
)


class PageNumberDetectionStage:
    """页码检测。

    - is_global = True（跨页运行）
    - 扫描所有页面的底部元素
    - 统计哪些正则表达式在多数页面中匹配
    - 将匹配的底部元素标记为 page_number 类型
    """

    @property
    def name(self) -> str:
        return "page_number_detection"

    @property
    def is_global(self) -> bool:
        return True

    def process(
        self,
        elements: list[dict],
        ctx: PipelineContext,
    ) -> list[dict]:
        # 按 page_index 分组，找到每个页面的最底部元素
        pages_bottom_elements: dict[int, dict] = {}

        for elem in elements:
            page_idx = elem.get("page_index", 0)
            bbox = elem.get("bbox", [])

            if len(bbox) < 4:
                continue

            y_bottom = bbox[3]  # bbox[3] 是下边界

            # 当前页面还没有底部元素，或当前元素的 y_bottom 更大（更靠下）
            if (
                page_idx not in pages_bottom_elements
                or y_bottom > pages_bottom_elements[page_idx].get("bbox", [0] * 4)[3]
            ):
                pages_bottom_elements[page_idx] = elem

        # 统计每个正则表达式匹配的页面数量
        pattern_matches: dict[str, set[int]] = {
            pattern: set() for pattern in PAGE_NUMBER_PATTERNS
        }

        for page_idx, bottom_elem in pages_bottom_elements.items():
            text = bottom_elem.get("text", "").strip()

            if not text:
                continue

            # 尝试每个正则表达式
            for pattern in PAGE_NUMBER_PATTERNS:
                if re.match(pattern, text):
                    pattern_matches[pattern].add(page_idx)
                    break  # 一旦匹配，不再尝试其他正则（避免重复计数）

        # 找出有效的正则表达式（匹配 >= 70% 的页面）
        total_pages = len(pages_bottom_elements)
        valid_patterns: list[str] = []

        if total_pages > 0:
            threshold = int(total_pages * PAGE_NUMBER_THRESHOLD_RATIO)

            for pattern, matched_pages in pattern_matches.items():
                if len(matched_pages) >= threshold:
                    valid_patterns.append(pattern)
                    ctx.stats[f"page_number_pattern_{pattern}"] = (
                        f"{len(matched_pages)}/{total_pages}"
                    )

        # 没有有效的正则表达式 → 直接返回
        if not valid_patterns:
            ctx.stats["page_number_detection"] = "no_valid_pattern"
            return elements

        # 将所有匹配有效正则的底部元素标记为 page_number
        marked_count = 0

        for elem in elements:
            page_idx = elem.get("page_index", 0)
            bbox = elem.get("bbox", [])

            if len(bbox) < 4:
                continue

            # 只处理底部元素
            if page_idx not in pages_bottom_elements:
                continue

            bottom_elem = pages_bottom_elements[page_idx]
            if elem is not bottom_elem and elem.get("bbox") != bottom_elem.get("bbox"):
                continue

            text = elem.get("text", "").strip()

            if not text:
                continue

            for pattern in valid_patterns:
                if re.match(pattern, text):
                    elem["type"] = "page_number"
                    elem["level"] = None  # 页码不需要层级
                    marked_count += 1
                    break

        ctx.stats["page_number_detected"] = marked_count
        ctx.stats["page_number_valid_patterns"] = valid_patterns

        return elements
