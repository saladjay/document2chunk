"""页面底图渲染与坐标换算。

- PDF：PyMuPDF (``fitz``) pixmap @ ``dpi``（72 DPI → 像素）。
- 图片（OCR/扫描件）：``PIL.Image.open`` 原图（bbox 已在像素空间，scale=1）。
- PyMuPDF 为可选依赖（``[pdf]`` extra）；缺失时调用方应降级为结构树视图。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Tuple

from PIL import Image

log = logging.getLogger(__name__)

PDF_EXTENSIONS = {".pdf"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".gif", ".webp"}


def has_pymupdf() -> bool:
    """是否安装了 PyMuPDF（PDF 页面渲染所必需）。"""
    try:
        import fitz  # noqa: F401  PyMuPDF
    except ImportError:
        return False
    return True


def is_pdf(path: str | Path) -> bool:
    return Path(path).suffix.lower() in PDF_EXTENSIONS


def is_image(path: str | Path) -> bool:
    return Path(path).suffix.lower() in IMAGE_EXTENSIONS


def pdf_to_pixel(bbox: List[float], scale: float) -> Tuple[int, int, int, int]:
    """PDF 坐标（72 DPI）→ 像素坐标：``coord × scale``。"""
    return (
        int(bbox[0] * scale),
        int(bbox[1] * scale),
        int(bbox[2] * scale),
        int(bbox[3] * scale),
    )


def scale_for(source_path: str | Path, dpi: int) -> float:
    """坐标→像素缩放比：PDF 用 ``dpi/72``，图片用 1.0（已是像素空间）。"""
    return dpi / 72.0 if is_pdf(source_path) else 1.0


def render_page_background(
    source_path: str | Path,
    page_index: int,
    dpi: int = 150,
) -> Image.Image:
    """渲染指定页为 RGB ``PIL.Image``。

    - PDF：PyMuPDF 渲染（需 ``[pdf]`` extra）。
    - 图片：直接打开（``page_index`` 仅 0 有效，单页）。
    """
    source_path = Path(source_path)
    if is_image(source_path):
        with Image.open(source_path) as im:
            return im.convert("RGB")

    if not has_pymupdf():
        raise MissingPyMuPDFError(
            "渲染 PDF 页面需要 PyMuPDF：pip install document2chunk[pdf]"
        )
    import fitz  # PyMuPDF

    doc = fitz.open(str(source_path))
    try:
        if page_index < 0 or page_index >= len(doc):
            raise IndexError(f"页码 {page_index} 超出范围（共 {len(doc)} 页）")
        page = doc[page_index]
        zoom = dpi / 72.0
        pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        return Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    finally:
        doc.close()


class MissingPyMuPDFError(RuntimeError):
    """渲染 PDF 底图时 PyMuPDF 缺失。调用方据此降级为结构树视图。"""
