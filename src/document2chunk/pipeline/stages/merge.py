"""段落合并 Stage。

将连续的行级 paragraph 元素按规则合并为段落级元素。

合并规则（不可变）：
1. type 必须都是 paragraph
2. level 必须相同（包括都是 None）
3. 字号差 ≤ 0.5pt
4. 字体名称必须完全相同

heading / title 同行（y 差 ≤ 5pt）且同级时也合并。
"""

from __future__ import annotations

from document2chunk.pipeline.base import PipelineContext


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

        merged: list[dict] = []
        current = self._copy_elem(elements[0])

        for elem in elements[1:]:
            if self._can_merge(current, elem):
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
    def _can_merge(elem1: dict, elem2: dict) -> bool:
        """判断两个连续元素是否可以合并。"""
        type1 = elem1.get("type")
        type2 = elem2.get("type")

        if type1 == "paragraph" and type2 == "paragraph":
            return MergeStage._can_merge_paragraphs(elem1, elem2)
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
    def _can_merge_paragraphs(elem1: dict, elem2: dict) -> bool:
        """判断两个 paragraph 是否可以合并。"""
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

        return True

    @staticmethod
    def _copy_elem(elem: dict) -> dict:
        """浅拷贝 element，深拷贝 spans 列表。"""
        copied = elem.copy()
        copied["spans"] = list(elem.get("spans", []))
        return copied
