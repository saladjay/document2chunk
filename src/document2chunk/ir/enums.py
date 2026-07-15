"""IR 枚举。所有枚举继承 ``str`` 以便直接 JSON 序列化。"""

from __future__ import annotations

from enum import Enum


class SourceType(str, Enum):
    """输入源类型。"""

    PDF = "pdf"  # 可编辑 PDF
    OCR = "ocr"  # 扫描件 / 图片
    DOCX = "docx"
    XLSX = "xlsx"  # 未来
    PPTX = "pptx"  # 未来
    HTML = "html"  # 未来


class BlockType(str, Enum):
    """块节点类型（判别联合的 discriminator）。"""

    HEADING = "heading"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    LIST = "list"
    IMAGE = "image"
    FORMULA = "formula"
    TOC = "toc"  # 可选导出（designs/001 D7）
    # 未来 / P3 预留：FOOTNOTE, COMMENT, REVISION, CONTENT_CONTROL


class InlineType(str, Enum):
    """行内节点类型。"""

    RUN = "run"
    HYPERLINK = "hyperlink"
    INLINE_FORMULA = "inline_formula"
