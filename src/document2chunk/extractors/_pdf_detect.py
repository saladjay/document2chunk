"""PDF 可编辑性检测（迁移自 ``doc-paddle-ocr/pdf_parsers/pdf_detect.py``，designs/003 §7）。

判定 PDF 为 editable / scanned / mixed，用于源路由（scanned/mixed → ocr-extractor）。
依据 ``docs/coding-standards.md`` §10：pymupdf 已声明，直连 ``import pymupdf``，
删旧 ``try: import fitz`` 兼容。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

# ==================== 阈值配置 ====================

MIN_TEXT_CHARS = 30  # 页面最少文本字符数（低于此视为无可提取文本）
MIN_IMAGE_COVERAGE = 0.3  # 图像面积占比阈值（超过此值视为扫描件）
LARGE_IMAGE_AREA_RATIO = 0.5  # 单图占页面面积 ≥50% 视为大截图/扫描图

DOCUMENT_RATIO = 0.7  # ≥70% 页面同类型才判定为纯类型


# ==================== 数据结构 ====================


@dataclass
class PageInfo:
    """单页检测结果。"""

    page_index: int
    type: str  # "editable" | "scanned" | "empty"
    text_chars: int = 0
    text_blocks: int = 0
    image_blocks: int = 0
    image_coverage: float = 0.0


@dataclass
class DetectResult:
    """PDF 检测结果。"""

    pdf_type: str  # "editable" | "scanned" | "mixed"
    total_pages: int = 0
    editable_pages: int = 0
    scanned_pages: int = 0
    empty_pages: int = 0
    pages: List[PageInfo] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "pdf_type": self.pdf_type,
            "total_pages": self.total_pages,
            "editable_pages": self.editable_pages,
            "scanned_pages": self.scanned_pages,
            "empty_pages": self.empty_pages,
            "pages": [
                {
                    "page_index": p.page_index,
                    "type": p.type,
                    "text_chars": p.text_chars,
                    "text_blocks": p.text_blocks,
                    "image_blocks": p.image_blocks,
                    "image_coverage": round(p.image_coverage, 3),
                }
                for p in self.pages
            ],
        }


# ==================== 核心检测 ====================


def _analyze_page(page) -> PageInfo:
    """分析单页的可编辑性特征。"""
    import pymupdf

    # 1. 提取纯文本，计算非空白字符数
    text = page.get_text("text")
    text_chars = len(text.replace(" ", "").replace("\n", "").replace("\t", ""))

    # 2. 获取块信息
    blocks = page.get_text("dict")["blocks"]
    text_blocks = sum(1 for b in blocks if b["type"] == 0)
    image_blocks_from_text = sum(1 for b in blocks if b["type"] == 1)

    # 3. 计算图像覆盖面积（从 get_text("dict") 的 blocks）
    page_area = page.rect.width * page.rect.height
    image_area_from_blocks = 0.0
    for b in blocks:
        if b["type"] == 1:
            r = pymupdf.Rect(b["bbox"])
            image_area_from_blocks += r.width * r.height

    # 4. 通过 get_image_info() 检测页面中的图片（更可靠的背景图检测）
    image_area_from_images = 0.0
    image_blocks_from_images = 0
    try:
        image_info_list = page.get_image_info()
        for img_info in image_info_list:
            bbox = img_info.get("bbox")
            if bbox:
                r = pymupdf.Rect(bbox)
                image_area_from_images += r.width * r.height
                image_blocks_from_images += 1
    except Exception:
        pass

    # 取两个来源的最大值作为图片覆盖面积
    image_area = max(image_area_from_blocks, image_area_from_images)
    image_blocks = max(image_blocks_from_text, image_blocks_from_images)
    image_coverage = image_area / page_area if page_area > 0 else 0.0

    # 5. 判定页面类型
    has_text = text_chars >= MIN_TEXT_CHARS
    has_large_image = image_coverage >= LARGE_IMAGE_AREA_RATIO
    has_some_image = image_coverage >= MIN_IMAGE_COVERAGE

    # 优先判断：有大截图的页面视为 scanned（即使有少量说明文字）
    if has_large_image:
        page_type = "scanned"
    elif has_text:
        page_type = "editable"
    elif has_some_image:
        page_type = "scanned"
    else:
        page_type = "empty"

    return PageInfo(
        page_index=0,  # 由调用者填充
        type=page_type,
        text_chars=text_chars,
        text_blocks=text_blocks,
        image_blocks=image_blocks,
        image_coverage=image_coverage,
    )


def detect_pdf_type(
    source: str | Path | bytes,
    pages: Optional[List[int]] = None,
) -> DetectResult:
    """检测 PDF 类型（可编辑 / 扫描件 / 混合）。

    Args:
        source: PDF 文件路径或二进制内容。
        pages: 指定检测的页码（0-based），None 表示全部。快速检测可传 [0, 1, 2]。
    """
    import pymupdf

    if isinstance(source, (bytes, bytearray)):
        doc = pymupdf.open(stream=bytes(source), filetype="pdf")
    else:
        pdf_path = Path(source)
        if not pdf_path.exists():
            raise FileNotFoundError(f"文件不存在: {pdf_path}")
        doc = pymupdf.open(str(pdf_path))
    total_pages = len(doc)

    if pages is not None:
        target_pages = [p for p in pages if 0 <= p < total_pages]
    else:
        target_pages = list(range(total_pages))

    page_infos: List[PageInfo] = []
    editable_count = 0
    scanned_count = 0
    empty_count = 0

    for page_idx in target_pages:
        page = doc[page_idx]
        info = _analyze_page(page)
        info.page_index = page_idx
        page_infos.append(info)

        if info.type == "editable":
            editable_count += 1
        elif info.type == "scanned":
            scanned_count += 1
        else:
            empty_count += 1

    doc.close()

    # 文档级判定
    checked = len(target_pages)
    if checked == 0:
        pdf_type = "empty"
    elif editable_count / checked >= DOCUMENT_RATIO:
        pdf_type = "editable"
    elif scanned_count / checked >= DOCUMENT_RATIO:
        pdf_type = "scanned"
    else:
        pdf_type = "mixed"

    return DetectResult(
        pdf_type=pdf_type,
        total_pages=total_pages,
        editable_pages=editable_count,
        scanned_pages=scanned_count,
        empty_pages=empty_count,
        pages=page_infos,
    )
