"""PageChunker —— PDF 按页切（PyMuPDF）；图片原样作单页。

长 PDF 整份送 unlimited-ocr 会 500（实测 49 页），故按页切分逐页送（澄清2 C7）。
"""

from __future__ import annotations

import io
from typing import Iterator, Tuple

import fitz  # PyMuPDF


def iter_pages(source_bytes: bytes, filename: str = "source") -> Iterator[Tuple[int, bytes, str, float, float]]:
    """yield (page_index_0based, media_bytes, media_filename, width, height)。

    PDF → 每页一个 1 页 PDF 子集，width/height 为**页面点尺寸**（PyMuPDF rect，72DPI）；
    图片 → 单页（原 bytes），width/height 为**像素尺寸**（PIL）。
    尺寸用于把 OCR 服务的 1000 归一化 bbox 换算到源自然坐标系（debug 可视化校准）。
    """
    if source_bytes[:4].lower() == b"%pdf":
        doc = fitz.open(stream=source_bytes, filetype="pdf")
        try:
            for i in range(len(doc)):
                page = doc[i]
                pw, ph = float(page.rect.width), float(page.rect.height)
                out = fitz.open()
                out.insert_pdf(doc, from_page=i, to_page=i)
                buf = io.BytesIO()
                out.save(buf)
                out.close()
                yield (i, buf.getvalue(), f"page_{i}.pdf", pw, ph)
        finally:
            doc.close()
    else:
        try:
            from PIL import Image

            with Image.open(io.BytesIO(source_bytes)) as im:
                pw, ph = float(im.size[0]), float(im.size[1])
        except Exception:
            pw = ph = 0.0
        yield (0, source_bytes, filename, pw, ph)


def page_count(source_bytes: bytes) -> int:
    if source_bytes[:4].lower() == b"%pdf":
        doc = fitz.open(stream=source_bytes, filetype="pdf")
        try:
            return len(doc)
        finally:
            doc.close()
    return 1
