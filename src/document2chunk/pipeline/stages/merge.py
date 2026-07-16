"""段落合并 Stage。

将连续的行级 paragraph 元素按规则合并为段落级元素。

合并规则：
1. type 必须都是 paragraph
2. level 必须相同（包括都是 None）
3. 字号差 ≤ 0.5pt
4. 字体名称必须完全相同
5. 垂直间距 ≤ 标准行间距 × :data:`_PARAGRAPH_BREAK_SPACING_RATIO`
   （间距过大说明是不同段落，不合并——修复「段落过度合并」）

标准行间距 = 相邻同款 paragraph 垂直间距的众数（:data:`_SPACING_BUCKET` pt 网格聚类）。
heading / title 同行（y 差 ≤ 5pt）且同级时也合并。
"""

from __future__ import annotations

from collections import Counter

from document2chunk.pipeline.base import PipelineContext

# 间距大于「标准行间距 × 此倍数」视为段落分隔（不合并）
_PARAGRAPH_BREAK_SPACING_RATIO = 1.5
# 间距聚类步长（pt）——消除浮点噪声后取众数
_SPACING_BUCKET = 0.1


class MergeStage:
    """段落合并。

    - is_global = False（逐页运行）
    - 合并连续 paragraph 行
    """

    @property
    def name(self) -> str:
        return "merge"

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

        # 先统计标准行间距（相邻同款 paragraph 垂直间距众数）
        standard_spacing = self._compute_standard_spacing(elements)

        merged: list[dict] = []
        current = self._copy_elem(elements[0])

        for elem in elements[1:]:
            if self._can_merge(current, elem, standard_spacing):
                # 合并文本
                current["text"] = current["text"] + elem["text"]
                current["markdown"] = current["markdown"] + elem["markdown"]

                # 传播低置信标记（OCR：任一组成行低置信则整段低置信；PDF 无此键，无副作用）
                if elem.get("low_confidence"):
                    current["low_confidence"] = True

                # 更新 bbox（取极值）
                cb = current["bbox"]
                eb = elem["bbox"]
                current["bbox"] = [
                    min(cb[0], eb[0]),
                    min(cb[1], eb[1]),
                    max(cb[2], eb[2]),
                    max(cb[3], eb[3]),
                ]

                # 合并 spans
                current["spans"].extend(elem.get("spans", []))
            else:
                merged.append(current)
                current = self._copy_elem(elem)

        merged.append(current)
        return merged

    @staticmethod
    def _can_merge(
        elem1: dict,
        elem2: dict,
        standard_spacing: float | None = None,
    ) -> bool:
        """判断两个连续元素是否可以合并。"""
        type1 = elem1.get("type")
        type2 = elem2.get("type")

        if type1 == "paragraph" and type2 == "paragraph":
            return MergeStage._can_merge_paragraphs(elem1, elem2, standard_spacing)
        if type1 in ("heading", "title") and type2 in ("heading", "title"):
            # heading/title 只有在同一行时才合并（y 坐标差 ≤ 5pt）
            bbox1 = elem1.get("bbox", [0] * 4)
            bbox2 = elem2.get("bbox", [0] * 4)
            if len(bbox1) >= 4 and len(bbox2) >= 4:
                y_diff = abs(bbox1[1] - bbox2[1])
                if y_diff <= 5:
                    return elem1.get("level") == elem2.get("level")

        return False

    @staticmethod
    def _can_merge_paragraphs(
        elem1: dict,
        elem2: dict,
        standard_spacing: float | None = None,
    ) -> bool:
        """判断两个 paragraph 是否可以合并（含行间距判断）。"""
        # 层级必须相同
        if elem1.get("level") != elem2.get("level"):
            return False

        # 字号差异 ≤ 0.5pt
        size1 = elem1.get("style", {}).get("size", 0)
        size2 = elem2.get("style", {}).get("size", 0)
        if abs(size1 - size2) > 0.5:
            return False

        # 字体名称必须相同
        font1 = elem1.get("style", {}).get("font", "")
        font2 = elem2.get("style", {}).get("font", "")
        if font1 != font2:
            return False

        # 行间距判断：垂直间距远大于标准行间距 → 不同段落，不合并
        if standard_spacing is not None and standard_spacing > 0:
            bbox1 = elem1.get("bbox", [0] * 4)
            bbox2 = elem2.get("bbox", [0] * 4)
            if len(bbox1) >= 4 and len(bbox2) >= 4:
                # 两元素可能上下排列或重叠，取最小非负间距
                y_gap = max(bbox2[1] - bbox1[3], bbox1[1] - bbox2[3], 0)
                if y_gap > standard_spacing * _PARAGRAPH_BREAK_SPACING_RATIO:
                    return False

        return True

    @staticmethod
    def _compute_standard_spacing(elements: list[dict]) -> float | None:
        """标准行间距：相邻同款 paragraph 垂直间距的众数。

        只统计相邻且同字体、字号差 ≤ 0.5pt 的 paragraph 对；间距按
        :data:`_SPACING_BUCKET` pt 网格聚类后取众数。无数据返回 None（退化为旧行为）。
        """
        spacings: list[float] = []
        for i in range(len(elements) - 1):
            e1, e2 = elements[i], elements[i + 1]
            if e1.get("type") != "paragraph" or e2.get("type") != "paragraph":
                continue
            s1 = e1.get("style", {})
            s2 = e2.get("style", {})
            if abs(s1.get("size", 0) - s2.get("size", 0)) > 0.5:
                continue
            if s1.get("font", "") != s2.get("font", ""):
                continue
            b1 = e1.get("bbox", [])
            b2 = e2.get("bbox", [])
            if len(b1) < 4 or len(b2) < 4:
                continue
            spacing = b2[1] - b1[3]  # 后者 top − 前者 bottom
            if spacing > 0:
                spacings.append(spacing)

        if not spacings:
            return None

        bucket = _SPACING_BUCKET
        rounded = [round(s / bucket) * bucket for s in spacings]
        counter = Counter(rounded)
        return counter.most_common(1)[0][0]

    @staticmethod
    def _copy_elem(elem: dict) -> dict:
        """浅拷贝 element，深拷贝 spans 列表。"""
        copied = elem.copy()
        copied["spans"] = list(elem.get("spans", []))
        return copied
