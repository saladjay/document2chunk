"""目录识别 Stage。

识别目录页并将目录条目标记为 toc_entry 类型。

识别策略：
1. 特征检测：文本中包含点线引导符（… / …… / .....）是目录条目的强信号
2. 页级判定：一页中 ≥3 个连续元素含点线 → 该页为目录页
3. 标记：
   - 目录页标题（"目录"/"目 录"/"Table of Contents"）→ toc_title
   - 含点线的条目 → toc_entry
   - 目录页上其他非空元素 → toc_entry（兜底）

设计约束：
- 在 ClassificationStage 之后运行（需要 type/level 已确定）
- 在 MergeStage 之前运行（toc_entry 不参与段落合并）
- is_global = False（逐页判断，目录页是页面级概念）
"""

from __future__ import annotations

import re

from document2chunk.pipeline.base import PipelineContext


# 点线引导符正则：连续3个以上的 . 或 … 或 ·
_DOT_LEADER_RE = re.compile(
    r"(?:\.{3,})"  # 英文点号连续3个以上: ...
    r"|(?:…{1,})"  # 水平省略号 … 或 ……
    r"|(?:·{3,})"  # 中间点连续3个以上: ···
    r"|(?:\.{2,}\s*\d)"  # 点号+空格+数字: ..8 或 .. 8
    r"|(?:(?<=\s)\.{2,}$)"  # 行尾连续点号
)

# 目录标题关键词（去除空格后匹配）
_TOC_TITLE_KEYWORDS = {
    "目录",
    "目 录",
    "目    录",
    "目   录",
    "目  录",
    "tableofcontents",
    "contents",
}


class TOCDetectionStage:
    """目录识别。

    - is_global = False（逐页运行）
    - 读取元素的 text，检测点线引导符模式
    - 将目录条目标记为 type=toc_entry，目录标题标记为 type=toc_title
    """

    @property
    def name(self) -> str:
        return "toc_detection"

    @property
    def is_global(self) -> bool:
        return False

    def process(
        self,
        elements: list[dict],
        ctx: PipelineContext,
    ) -> list[dict]:
        if not elements:
            return elements

        # Step 1: 标记每个元素是否含点线
        has_dot_flags = [self._has_dot_leader(e) for e in elements]

        # Step 2: 检查是否有连续 ≥3 个元素含点线
        if not self._has_consecutive_dots(has_dot_flags, min_run=3):
            return elements

        # Step 3: 确认是目录页，标记所有相关元素
        toc_count = 0
        for i, elem in enumerate(elements):
            text = elem.get("text", "").strip()
            if not text:
                continue

            # 检查是否为目录标题
            if self._is_toc_title(text, elem):
                elem["type"] = "toc_title"
                elem["level"] = None
                toc_count += 1
                continue

            # 含点线的元素 → 目录条目
            if has_dot_flags[i]:
                elem["type"] = "toc_entry"
                elem["level"] = None
                toc_count += 1
                continue

            # 目录页上的其他元素（如页码、标题前缀等），如果非正文也标记
            elem_type = elem.get("type", "")
            if elem_type not in ("paragraph",) and has_dot_flags[i]:
                elem["type"] = "toc_entry"
                elem["level"] = None
                toc_count += 1

        if toc_count > 0:
            ctx.stats["toc_entries"] = ctx.stats.get("toc_entries", 0) + toc_count
            ctx.stats["toc_pages"] = ctx.stats.get("toc_pages", 0) + 1

        # Step 4: 合并同行的孤立章节号（如 "3.2.1" 与后面的 toc_entry）
        elements = self._merge_section_with_entry(elements)

        return elements

    # 章节号正则：匹配 "3.2.1"、"3.1"、"第一章"、"一、" 等纯章节编号
    _SECTION_NUM_RE = re.compile(
        r"^("
        r"\d+(?:\.\d+)*"  # 3.2.1, 3.1, 1
        r"|第[一二三四五六七八九十百千]+[章节条篇部]"  # 第一章, 第二节
        r"|[一二三四五六七八九十]+、"  # 一、, 二、
        r"|[（(][一二三四五六七八九十]+[）)]"  # （一）, (二)
        r")\s*$"
    )

    @classmethod
    def _merge_section_with_entry(cls, elements: list[dict]) -> list[dict]:
        """合并同行的孤立章节号与后续 toc_entry。

        PDF 提取时，目录条目的章节号（如 "3.2.1"）和标题文本
        （如 "科技项目管理入口...9"）可能被拆成两个独立元素。
        若两者 y 坐标接近（同行），则合并为一个 toc_entry。
        """
        if not elements:
            return elements

        result = []
        i = 0
        while i < len(elements):
            curr = elements[i]
            curr_type = curr.get("type", "")

            # 检查：当前元素是章节号，下一个元素是 toc_entry
            if (
                i + 1 < len(elements)
                and curr_type not in ("toc_entry", "toc_title", "table", "image")
                and elements[i + 1].get("type") == "toc_entry"
            ):
                next_elem = elements[i + 1]
                curr_text = curr.get("text", "").strip()
                curr_bbox = curr.get("bbox", [])
                next_bbox = next_elem.get("bbox", [])

                if (
                    curr_text
                    and cls._SECTION_NUM_RE.match(curr_text)
                    and len(curr_bbox) >= 4
                    and len(next_bbox) >= 4
                ):
                    y_diff = abs(curr_bbox[1] - next_bbox[1])
                    x_gap = next_bbox[0] - curr_bbox[2]
                    same_line_strict = y_diff <= 10
                    same_line_loose = y_diff <= 35 and -50 <= x_gap <= 200

                    if same_line_strict or same_line_loose:
                        # 合并：章节号 + 空格 + toc_entry
                        merged = next_elem.copy()
                        next_text = next_elem.get("text", "")
                        next_md = next_elem.get("markdown", "")
                        merged["text"] = curr_text + " " + next_text
                        merged["markdown"] = (
                            curr.get("markdown", curr_text) + " " + next_md
                        )
                        merged["type"] = "toc_entry"
                        merged["level"] = None
                        merged["bbox"] = [
                            min(curr_bbox[0], next_bbox[0]),
                            min(curr_bbox[1], next_bbox[1]),
                            max(curr_bbox[2], next_bbox[2]),
                            max(curr_bbox[3], next_bbox[3]),
                        ]
                        merged["spans"] = list(curr.get("spans", [])) + list(
                            next_elem.get("spans", [])
                        )
                        result.append(merged)
                        i += 2
                        continue

            result.append(curr)
            i += 1

        return result

    @staticmethod
    def _has_dot_leader(elem: dict) -> bool:
        """检查元素文本是否包含点线引导符。"""
        text = elem.get("text", "")

        if _DOT_LEADER_RE.search(text):
            return True

        # 额外检查：纯文本中的 "...." 模式（可能跨 span）
        dot_count = 0
        for ch in text:
            if ch == ".":
                dot_count += 1
                if dot_count >= 4:
                    return True
            elif ch == "…":  # …
                return True
            else:
                dot_count = 0

        return False

    @staticmethod
    def _is_toc_title(text: str, elem: dict) -> bool:
        """判断元素是否为目录标题（如 "目录"、"目   录"）。"""
        stripped = re.sub(r"\s+", "", text)
        if stripped.lower() in _TOC_TITLE_KEYWORDS:
            return True

        if text.strip() in _TOC_TITLE_KEYWORDS:
            return True

        return False

    @staticmethod
    def _has_consecutive_dots(flags: list[bool], min_run: int = 3) -> bool:
        """检查是否存在连续 ≥ min_run 个 True。"""
        run = 0
        for flag in flags:
            if flag:
                run += 1
                if run >= min_run:
                    return True
            else:
                run = 0
        return False
