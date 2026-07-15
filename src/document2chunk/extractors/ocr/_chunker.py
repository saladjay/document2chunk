"""PageChunker —— PDF 按页切（PyMuPDF）；图片原样作单页。

长 PDF 整份送 unlimited-ocr 会 500（实测 49 页），故按页切分逐页送（澄清2 C7）。
"""

from __future__ import annotations

import io
from typing import Iterator, Tuple

import fitz  # PyMuPDF


def iter_pages(source_bytes: bytes, filename: str = "source") -> Iterator[Tuple[int, bytes, str]]:
    """yield (page_index_0based, media_bytes, media_filename)。

    PDF → 每页一个 1 页 PDF 子集；图片 → 单页（原 bytes）。
    """
    if source_bytes[:4].lower() == b"%pdf":
        doc = fitz.open(stream=source_bytes, filetype="pdf")
        try:
            for i in range(len(doc)):
                out = fitz.open()
                out.insert_pdf(doc, from_page=i, to_page=i)
                buf = io.BytesIO()
                out.save(buf)
                out.close()
                yield (i, buf.getvalue(), f"page_{i}.pdf")
        finally:
            doc.close()
    else:
        yield (0, source_bytes, filename)


def page_count(source_bytes: bytes) -> int:
    if source_bytes[:4].lower() == b"%pdf":
        doc = fitz.open(stream=source_bytes, filetype="pdf")
        try:
            return len(doc)
        finally:
            doc.close()
    return 1
