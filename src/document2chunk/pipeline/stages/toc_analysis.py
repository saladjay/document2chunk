"""目录分析 Stage。

利用已识别的目录条目（toc_entry），推断标题层级结构：
1. 提取章节号，通过 depth 推断层级（优先级最高）
2. 按 (font, size) 对 TOC 条目分组
3. 按 x0 缩进排序确定层级深度（缩进越小 = 层级越高）作为兜底
4. 构建 toc_text → heading_level 映射表
5. 在正文中匹配标题文本，精确分配层级

设计约束：
- 在 TOCDetectionStage 之后运行（需要 toc_entry 已标记）
- 在 MergeStage 之前运行（层级信息影响合并决策）
- is_global = True（跨页运行：目录页提供信息，正文页消费信息）
"""

from __future__ import annotations

import re

from document2chunk.pipeline.base import PipelineContext
from document2chunk.pipeline.common import normalize_font_size
from document2chunk.pipeline.heading_scorer import (
    HeadingScoreAccumulator,
    extract_section_number,
    is_pure_section_number,
    section_number_depth,
)

# TOC 文本匹配置信度
_TOC_SCORE_EXACT = 0.70  # 精确匹配
_TOC_SCORE_PREFIX = 0.60  # 前缀匹配（≥4字）
_TOC_SCORE_CLEANED = 0.55  # 去尾标点重试

# TOC 章节号 depth 推断置信度
_TOC_SCORE_DEPTH = 0.65


class TOCAnalysisStage:
    """基于目录分析标题层级。

    - is_global = True（跨页运行）
    - Phase 1: 从 toc_entry 提取层级映射（章节号 depth 优先，x0 缩进兜底）
    - Phase 2: 对正文中的 paragraph 元素进行文本匹配和层级分配
    """

    @property
    def name(self) -> str:
        return "toc_analysis"

    @property
    def is_global(self) -> bool:
        return True

    def process(
        self,
        elements: list[dict],
        ctx: PipelineContext,
    ) -> list[dict]:
        # Phase 1: 收集 TOC 条目，构建层级映射
        toc_mapping = self._build_toc_mapping(elements)

        if not toc_mapping:
            # 没有目录映射，对所有 toc_entry 记录 skip
            for elem in elements:
                if elem.get("type") == "toc_entry":
                    scorer = HeadingScoreAccumulator(elem)
                    scorer.skip("toc_analysis", "no_toc_mapping", note="未构建到目录映射")
                    scorer.apply_to(elem)
            return elements

        # 写入 context 供后续 stage 使用
        ctx.stats["toc_mapping_count"] = len(toc_mapping)

        # Phase 2: 匹配正文标题
        match_count = 0
        for elem in elements:
            scorer = HeadingScoreAccumulator(elem)
            elem_type = elem.get("type")

            # TOC 条目本身 → 记录 skip
            if elem_type in ("toc_entry", "toc_title"):
                scorer.skip(
                    "toc_analysis",
                    "toc_entry_skip",
                    note=f"{elem_type} 类型不参与正文匹配",
                )
                scorer.apply_to(elem)
                continue

            # 只处理 paragraph（包括被 Classification 标为 paragraph 的潜在标题）
            if elem_type not in ("paragraph", None):
                scorer.apply_to(elem)
                continue

            # 已有高置信度 level（≥0.50）→ skip
            if elem.get("level") is not None and scorer.confidence >= 0.50:
                scorer.skip(
                    "toc_analysis",
                    "high_confidence_skip",
                    note=f"已有 level={elem['level']}, confidence={scorer.confidence:.2f}",
                )
                scorer.apply_to(elem)
                continue

            text = elem.get("text", "").strip()
            if not text:
                scorer.apply_to(elem)
                continue

            # 跳过纯章节号的段落
            if is_pure_section_number(text):
                scorer.apply_to(elem)
                continue

            # 尝试文本匹配
            level, match_type = self._match_heading_with_type(text, toc_mapping)
            if level is not None:
                score_map = {
                    "exact": _TOC_SCORE_EXACT,
                    "prefix": _TOC_SCORE_PREFIX,
                    "cleaned": _TOC_SCORE_CLEANED,
                }
                score = score_map.get(match_type, _TOC_SCORE_CLEANED)

                old_level = elem.get("level")
                elem["level"] = level
                elem["type"] = "heading"

                action = "override" if old_level is not None else "assign"
                note = (
                    f"覆盖 level={old_level} → level={level}"
                    if old_level is not None
                    else ""
                )

                scorer.add_score(
                    stage="toc_analysis",
                    rule=f"text_{match_type}_match",
                    score=score,
                    action=action,
                    note=note,
                )
                match_count += 1
            else:
                scorer.skip(
                    "toc_analysis",
                    "no_text_match",
                    note="正文文本未在目录映射中匹配到",
                )

            scorer.apply_to(elem)

        if match_count > 0:
            ctx.stats["toc_matched_headings"] = match_count

        return elements

    def _build_toc_mapping(self, elements: list[dict]) -> dict[str, int]:
        """从 TOC 条目构建 {heading_text: level} 映射。

        策略（优先级从高到低）：
        1. 章节号 depth 推断（最可靠）
        2. x0 缩进排序（兜底）
        """
        toc_entries = []
        for elem in elements:
            if elem.get("type") == "toc_entry":
                raw_text = elem.get("text", "")
                text = self._clean_toc_text(raw_text)
                if not text:
                    continue

                # 提取章节号
                sec_num = extract_section_number(text)
                depth = section_number_depth(sec_num) if sec_num else 0

                # 清理文本：去除章节号前缀，保留标题文本
                if sec_num:
                    title_text = text[len(sec_num):].strip()
                else:
                    title_text = text

                # 跳过纯章节号
                if not title_text or is_pure_section_number(title_text):
                    continue

                toc_entries.append(
                    {
                        "text": title_text,
                        "full_text": text,
                        "font": elem.get("style", {}).get("font", ""),
                        "size": elem.get("style", {}).get("size", 0),
                        "x0": elem.get("bbox", [0])[0],
                        "section_number": sec_num,
                        "depth": depth,
                    }
                )

        if not toc_entries:
            return {}

        # 检查是否有足够的章节号 depth 信息
        has_depth = sum(1 for e in toc_entries if e["depth"] > 0)
        depth_ratio = has_depth / len(toc_entries) if toc_entries else 0

        if depth_ratio >= 0.5:
            return self._build_mapping_by_depth(toc_entries)
        return self._build_mapping_by_indent(toc_entries)

    def _build_mapping_by_depth(self, toc_entries: list[dict]) -> dict[str, int]:
        """通过章节号 depth 推断层级。

        depth=1 → level 2（文档主标题占 level 1）
        depth=2 → level 3
        depth=3 → level 4
        """
        level_offset = 1  # 文档主标题占 level 1

        mapping: dict[str, int] = {}
        for entry in toc_entries:
            level = entry["depth"] + level_offset
            mapping[entry["text"]] = level

        return mapping

    def _build_mapping_by_indent(self, toc_entries: list[dict]) -> dict[str, int]:
        """通过 x0 缩进排序推断层级（兜底方案）。"""
        # 按 (font, size) 分组，计算每组的平均 x0
        groups: dict[tuple[str, float], list[dict]] = {}
        for entry in toc_entries:
            key = (entry["font"], normalize_font_size(entry["size"]))
            groups.setdefault(key, []).append(entry)

        # 按平均 x0 排序（缩进越小越靠前）
        sorted_groups = sorted(
            groups.items(),
            key=lambda item: sum(e["x0"] for e in item[1]) / len(item[1]),
        )

        level_offset = 1  # TOC 最顶层 → heading level 2

        mapping: dict[str, int] = {}
        for group_idx, (_, entries) in enumerate(sorted_groups):
            level = group_idx + level_offset + 1
            for entry in entries:
                mapping[entry["text"]] = level

        return mapping

    def _match_heading_with_type(
        self, text: str, toc_mapping: dict[str, int]
    ) -> tuple[int | None, str | None]:
        """尝试将正文标题文本与 TOC 映射匹配。

        Returns:
            (level, match_type) 或 (None, None)；match_type: exact/prefix/cleaned
        """
        # 策略 1: 精确匹配
        if text in toc_mapping:
            return toc_mapping[text], "exact"

        # 策略 2: 前缀匹配
        for toc_text, level in toc_mapping.items():
            if len(toc_text) >= 4 and text.startswith(toc_text):
                return level, "prefix"

        # 策略 3: 去除末尾标点后重试
        cleaned = re.sub(r"[。，,.\s]+$", "", text)
        if cleaned != text and len(cleaned) >= 4 and cleaned in toc_mapping:
            return toc_mapping[cleaned], "cleaned"

        return None, None

    @staticmethod
    def _clean_toc_text(text: str) -> str:
        """清理 TOC 条目文本：去除点线引导符和页码。

        Examples:
            "一、现状与形势 ............. 2" → "一、现状与形势"
            "（一）发展基础 ....... 2" → "（一）发展基础"
        """
        # 按连续点号或省略号分割，取第一部分
        parts = re.split(r"\.{2,}|…+|·{2,}", text)
        clean = parts[0].strip()

        # 去除尾部空格和数字（页码）
        clean = re.sub(r"\s*\d+\s*$", "", clean)

        return clean.strip()
