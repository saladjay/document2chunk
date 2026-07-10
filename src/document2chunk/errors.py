"""document2chunk 异常层次。

各顶层模块定义自己的异常子类（继承 :class:`Document2ChunkError`），
层次清晰、便于调用方按粒度捕获。依据 ``docs/coding-standards.md`` §7。
"""

from __future__ import annotations


class Document2ChunkError(Exception):
    """所有 document2chunk 异常的基类。"""


class InvalidSourceError(Document2ChunkError):
    """源文件不可用：格式不支持、文件损坏、或源类型与 extractor 不匹配。

    例如把扫描件 PDF 交给 pdf-extractor（应路由到 ocr-extractor）。
    """


class ExtractionError(Document2ChunkError):
    """extractor 提取过程中的非局部异常（局部失败应 WARN + 跳过，不抛此异常）。"""


class PipelineError(Document2ChunkError):
    """span 管线编排异常。"""


class OptionalDependencyError(Document2ChunkError):
    """可选依赖（PyMuPDF/pdfplumber/PaddleOCR/lxml）缺失。"""
