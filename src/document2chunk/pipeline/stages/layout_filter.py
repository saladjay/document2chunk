"""版面分析几何工具（designs/009 后仅保留工具函数）。

噪声过滤职责（旧 LayoutFilterStage）已上移到 :func:`document2chunk.postprocess.filter_noise`
（跨页证据 + layout 框）。本模块只保留坐标换算 + box 提取工具，供 image_detection
图分类、pdf 表格校验、postprocess.filter_noise 复用。
"""

from __future__ import annotations

import json
from pathlib import Path

# 版面分析模型内部 DPI（LayoutDetection 默认值）—— 版面坐标 136 ↔ PDF 72pt 换算。
# 注意：这与 config.OCR_DPI=200（OCR 栅格化）是两套量纲，不可合并（designs/003 §7）。
LAYOUT_DPI = 136
PDF_DPI = 72


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

    读取 ``result.res.boxes[].{label, coordinate}``。供 image_detection（图分类）、
    pdf 表格校验、postprocess.filter_noise（layout 强证据）复用（designs/004/009）。
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
