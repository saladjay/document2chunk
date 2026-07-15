"""结构化源解析器（markdown / 未来 html）→ IR。

D11 后 OCR 服务返回结构化 markdown，本包提供共享 ``markdown → IR`` 解析器，
供 :mod:`document2chunk.extractors.ocr`（以及未来的 markdown/html-extractor）复用。
"""

from document2chunk.parsers.markdown import markdown_to_blocks

__all__ = ["markdown_to_blocks"]
