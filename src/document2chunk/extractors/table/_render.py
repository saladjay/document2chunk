"""PDF 页 → PIL 图（供 ``geo_ocr`` 图片调服务 + 本地 OCR）。

**lazy import fitz**（pymupdf）——仅 ``geo_ocr`` 模式需要，不污染 table 包的轻依赖。

渲染应用页面的 ``/Rotate``（= PDF 查看器朝向），使表格为水平阅读方向；服务在此图上
检测得到的 ``cell_box_list`` 与本图同像素空间，故可crop/匹配（校准 designs/008 §13）。
"""

from __future__ import annotations

from typing import Union

# 默认渲染 DPI（须与服务处理该图时的内部分辨率无关——我们送整图给服务，服务框即图空间）
_DEFAULT_DPI = 200


def render_pdf_page(pdf_bytes: bytes, page_index: int, *, dpi: int = _DEFAULT_DPI):
    """渲染 PDF 第 ``page_index`` 页（0-based）→ ``PIL.Image``（RGB）。

    Args:
        pdf_bytes: PDF 二进制。
        page_index: 0-based 页码。
        dpi: 渲染分辨率（默认 200）。
    """
    import fitz  # pymupdf（lazy）
    from PIL import Image

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        if page_index < 0 or page_index >= doc.page_count:
            raise IndexError(f"页码 {page_index} 越界（共 {doc.page_count} 页）")
        page = doc.load_page(page_index)
        # get_pixmap 默认应用 /Rotate → 与 PDF 查看器一致（表格水平阅读）
        pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72))
        mode = "RGB" if pix.alpha == 0 else "RGBA"
        img = Image.frombytes(mode, (pix.width, pix.height), pix.samples)
    finally:
        doc.close()
    return img.convert("RGB") if img.mode != "RGB" else img


def image_to_png_bytes(img) -> bytes:
    """PIL.Image → PNG bytes（送服务用）。"""
    from io import BytesIO

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
