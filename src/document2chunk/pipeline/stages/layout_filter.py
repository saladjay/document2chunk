"""版面分析过滤 Stage。

利用 PaddleOCR LayoutDetection 的版面分析结果，过滤页码、页眉、页脚等非正文元素。
匹配策略：中心点匹配 + 5% 页面高度扩展框（补偿坐标系对齐误差）。
"""

from __future__ import annotations

import json
from pathlib import Path

from document2chunk.pipeline.base import PipelineContext


# 需要过滤掉的非正文类别
NON_BODY_LABELS = {
    "number",  # 页码
    "header",  # 页眉
    "footer",  # 页脚
    "page_header",
    "page_footer",
    "page_number",
}

# 版面分析模型内部 DPI（LayoutDetection 默认值）—— 版面坐标 136 ↔ PDF 72pt 换算。
# 注意：这与 config.OCR_DPI=200（OCR 栅格化）是两套量纲，不可合并（designs/003 §7）。
LAYOUT_DPI = 136
PDF_DPI = 72


class LayoutFilterStage:
    """版面分析过滤。

    - is_global = False（逐页运行）
    - 读取 ctx.layout_data, ctx.page_index, ctx.page_width, ctx.page_height
    - 移除匹配非正文区域的元素
    - 支持启发式页眉页脚检测（当版面分析未检测到 header/footer 时）
    """

    def __init__(
        self,
        layout_jsonl: str | None = None,
        enable_heuristic_header_footer: bool = True,
    ):
        """
        Args:
            layout_jsonl: PaddleOCR LayoutDetection 输出的 JSONL 文件路径（可选）。
            enable_heuristic_header_footer: 版面分析未检测到 header/footer 时，
                是否启用启发式顶/底 8% 过滤（默认 True）。
        """
        self.layout_jsonl = layout_jsonl
        self._cached_layout_data = None
        self.enable_heuristic_header_footer = enable_heuristic_header_footer

    @property
    def name(self) -> str:
        return "layout_filter"

    @property
    def is_global(self) -> bool:
        return False

    def process(
        self,
        elements: list[dict],
        ctx: PipelineContext,
    ) -> list[dict]:
        # 如果构造时提供了 layout_jsonl，加载并缓存
        if self.layout_jsonl and self._cached_layout_data is None:
            self._cached_layout_data = load_layout_data(self.layout_jsonl)

        # 优先使用传入的 layout_data（来自提取前端），否则使用缓存的
        layout_data = ctx.layout_data or self._cached_layout_data

        page_idx = ctx.page_index
        page_width = ctx.page_width
        page_height = ctx.page_height

        # 检测当前页是否为目录页（如果有 toc_entry 元素）
        is_toc_page = any(e.get("type") == "toc_entry" for e in elements)

        # 获取非正文区域的 PDF 坐标框（从版面分析）
        non_body_boxes: list[list[float]] = []
        has_header_footer_detection = False

        if layout_data and page_idx < len(layout_data):
            page_layout = layout_data[page_idx]
            non_body_boxes = self._extract_non_body_boxes(
                page_layout, page_width, page_height
            )
            has_header_footer_detection = self._has_header_footer_detected(page_layout)

        # 没检测到 header/footer 且启用启发式 → 添加顶/底 8% 框
        # 目录页跳过启发式过滤，避免误删目录条目
        if (
            not has_header_footer_detection
            and self.enable_heuristic_header_footer
            and not is_toc_page
        ):
            non_body_boxes.extend(
                self._add_heuristic_header_footer_boxes(page_width, page_height)
            )

        if not non_body_boxes:
            return elements

        # 过滤元素：检查元素中心点是否在扩展框内
        filtered = []
        for elem in elements:
            elem_bbox = elem.get("bbox", [])
            if len(elem_bbox) < 4:
                filtered.append(elem)
                continue

            cx = (elem_bbox[0] + elem_bbox[2]) / 2
            cy = (elem_bbox[1] + elem_bbox[3]) / 2

            is_non_body = False
            for b in non_body_boxes:
                if b[0] <= cx <= b[2] and b[1] <= cy <= b[3]:
                    is_non_body = True
                    break

            if not is_non_body:
                filtered.append(elem)

        return filtered

    def _extract_non_body_boxes(
        self,
        page_layout: dict,
        page_width: float,
        page_height: float,
    ) -> list[list[float]]:
        """从版面分析结果中提取非正文区域的扩展框（PDF 坐标）。"""
        non_body_boxes: list[list[float]] = []
        res = page_layout.get("result", {}).get("res", {})
        boxes = res.get("boxes", [])

        # 扩展量：页面高度的 5%（约 42pt for A4）
        expand = page_height * 0.05

        for box in boxes:
            label = box.get("label", "")
            if label in NON_BODY_LABELS:
                coord = box.get("coordinate", [])
                pdf_coord = self._layout_to_pdf_coords(
                    coord, page_width, page_height
                )
                expanded = [
                    pdf_coord[0] - expand,
                    pdf_coord[1] - expand,
                    pdf_coord[2] + expand,
                    pdf_coord[3] + expand,
                ]
                non_body_boxes.append(expanded)

        return non_body_boxes

    @staticmethod
    def _layout_to_pdf_coords(
        coord: list[float],
        page_width: float,
        page_height: float,
    ) -> list[float]:
        """将版面分析坐标（DPI=136）转换为 PDF 坐标（DPI=72）。"""
        if len(coord) < 4:
            return coord

        layout_width = page_width * (LAYOUT_DPI / PDF_DPI)
        layout_height = page_height * (LAYOUT_DPI / PDF_DPI)

        return [
            coord[0] / layout_width * page_width,
            coord[1] / layout_height * page_height,
            coord[2] / layout_width * page_width,
            coord[3] / layout_height * page_height,
        ]

    @staticmethod
    def _has_header_footer_detected(page_layout: dict) -> bool:
        """检查版面分析是否检测到了 header/footer。"""
        res = page_layout.get("result", {}).get("res", {})
        boxes = res.get("boxes", [])

        for box in boxes:
            label = box.get("label", "")
            if label in {"header", "footer", "page_header", "page_footer"}:
                return True
        return False

    @staticmethod
    def _add_heuristic_header_footer_boxes(
        page_width: float,
        page_height: float,
    ) -> list[list[float]]:
        """启发式页眉页脚检测框：顶部 8% + 底部 8%。"""
        header_height = page_height * 0.08
        footer_height = page_height * 0.08

        return [
            [0, 0, page_width, header_height],
            [0, page_height - footer_height, page_width, page_height],
        ]


def load_layout_data(layout_jsonl: str | Path) -> list[dict]:
    """加载版面分析 JSONL 文件，按 page_index 排序。"""
    records = []
    with open(layout_jsonl, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    records.sort(key=lambda r: r.get("page_index", 0))
    return records


def layout_to_pdf_coords(
    coord: list[float], page_width: float, page_height: float
) -> list[float]:
    """版面分析坐标（DPI=136）→ PDF 坐标（DPI=72）。共享给 image_detection / pdf 表格校验。"""
    if len(coord) < 4 or page_width <= 0 or page_height <= 0:
        return coord
    lw = page_width * (LAYOUT_DPI / PDF_DPI)
    lh = page_height * (LAYOUT_DPI / PDF_DPI)
    return [
        coord[0] / lw * page_width,
        coord[1] / lh * page_height,
        coord[2] / lw * page_width,
        coord[3] / lh * page_height,
    ]


def layout_boxes_for_page(
    layout_data, page_idx: int, page_width: float, page_height: float
) -> list[tuple[str, list[float]]]:
    """取某页全部版面 box → [(label, pdf_bbox), ...]。

    结构同 :meth:`LayoutFilterStage` 读取：``result.res.boxes[].{label, coordinate}``。
    供 image_detection（图分类）与 pdf 表格校验复用（designs/004）。
    """
    if not layout_data or page_idx >= len(layout_data) or page_width <= 0:
        return []
    page_layout = layout_data[page_idx]
    boxes = page_layout.get("result", {}).get("res", {}).get("boxes", []) or []
    out: list[tuple[str, list[float]]] = []
    for box in boxes:
        coord = box.get("coordinate") or box.get("bbox") or []
        if len(coord) >= 4:
            out.append(
                (str(box.get("label", "")).lower(), layout_to_pdf_coords(coord, page_width, page_height))
            )
    return out
