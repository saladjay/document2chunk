"""测试用 PDF 夹具生成器。

用 PyMuPDF 在运行时生成可编辑 PDF（英文文本，字体兼容、确定性）。
注：本机 PyMuPDF 的内存文档存在 Shape 绘制不持久的问题（get_drawings 恒空），
故不在此生成矢量表格；表格走 ``test_pdf_extractor.test_table_mapping`` 单测。

所有生成器返回 PDF 二进制（``bytes``），供 :class:`PdfExtractor` 消费。
"""

from __future__ import annotations

from typing import Optional


def _new_doc():
    import pymupdf

    return pymupdf.open()


def _text_page(doc, *, title: Optional[str], heading: Optional[str], body_lines: list[str], page_no: Optional[int], width=595, height=842):
    import pymupdf

    page = doc.new_page(width=width, height=height)
    y = 80
    if title:
        page.insert_text((72, y), title, fontsize=22, fontname="helv")
        y += 60
    if heading:
        page.insert_text((72, y), heading, fontsize=16, fontname="helv")
        y += 40
    for line in body_lines:
        page.insert_text((72, y), line, fontsize=12, fontname="helv")
        y += 20
    if page_no is not None:
        page.insert_text((280, 820), str(page_no), fontsize=10, fontname="helv")
    return page


def make_simple_pdf() -> bytes:
    """单页：大标题 + 二级标题 + 两行正文 + 页码。"""
    doc = _new_doc()
    _text_page(
        doc,
        title="Document Title",
        heading="1.1 Section",
        body_lines=["Body line one here.", "Body line two here."],
        page_no=1,
    )
    data = doc.tobytes()
    doc.close()
    return data


def make_multipage_pdf(n: int = 4) -> bytes:
    """多页：每页一个二级标题 + 正文 + 页码（≥70% 页有页码 → 触发页码检测）。"""
    doc = _new_doc()
    for i in range(n):
        _text_page(
            doc,
            title=("Document Title" if i == 0 else None),
            heading=f"{i+1}.0 Page Section",
            body_lines=[f"Page {i+1} body content here."],
            page_no=i + 1,
        )
    data = doc.tobytes()
    doc.close()
    return data


def make_toc_pdf() -> bytes:
    """带目录页：第 1 页正文，第 2 页目录（点线引导），第 3 页正文。

    目录条目使用点线引导符（.....），触发 TOCDetection（≥3 连续点线条目）。
    """
    doc = _new_doc()
    # page 0: 正文
    p0 = doc.new_page(width=595, height=842)
    p0.insert_text((72, 80), "Document Title", fontsize=22, fontname="helv")
    p0.insert_text((72, 140), "1.1 Overview", fontsize=16, fontname="helv")
    p0.insert_text((72, 170), "Intro body text.", fontsize=12, fontname="helv")
    # page 1: 目录（点线引导）
    p1 = doc.new_page(width=595, height=842)
    p1.insert_text((72, 80), "Contents", fontsize=18, fontname="helv")
    for i, (title, pg) in enumerate(
        [("1.1 Overview", 1), ("1.2 Background", 2), ("1.3 Methods", 3)]
    ):
        # 点线引导：标题 .......... 页码
        dots = "." * 10
        p1.insert_text((72, 120 + i * 24), f"{title} {dots} {pg}", fontsize=12, fontname="helv")
    # page 2: 正文
    p2 = doc.new_page(width=595, height=842)
    p2.insert_text((72, 80), "1.2 Background", fontsize=16, fontname="helv")
    p2.insert_text((72, 120), "Background body text.", fontsize=12, fontname="helv")
    data = doc.tobytes()
    doc.close()
    return data


def make_scanned_pdf_bytes() -> bytes:
    """扫描件 PDF（页面是整页图片、无可提取文本）→ 用于 detect_pdf_type 的 scanned 判定。"""
    import pymupdf
    from PIL import Image

    img = Image.new("RGB", (1240, 1754), "white")
    import io

    buf = io.BytesIO()
    img.save(buf, format="png")
    png = buf.getvalue()

    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(page.rect, stream=png)
    data = doc.tobytes()
    doc.close()
    return data
