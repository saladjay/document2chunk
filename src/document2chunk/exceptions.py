"""document2chunk 顶层异常基类与通用异常。

各 extractor / 模块定义自己的异常子类时，应继承 :class:`Document2ChunkError`
（见 ``docs/coding-standards.md`` §7）。本模块为所有模块**共享**（加性扩展，
不修改冻结的 ir-model）。
"""

from __future__ import annotations


class Document2ChunkError(Exception):
    """所有 document2chunk 异常的基类。"""


class UnsupportedFormatError(Document2ChunkError):
    """输入格式不受支持 / 路由失败。"""


class MissingDependencyError(Document2ChunkError):
    """可选依赖缺失（PyMuPDF / lxml / PaddleOCR 等），或对应模块尚未就绪。"""


class InvalidSourceError(Document2ChunkError):
    """源文件损坏 / 缺失关键部分（fast fail）。"""


class ExtractionError(Document2ChunkError):
    """extractor 提取过程中的非局部异常（局部失败应 WARN + 跳过，不抛此异常）。"""


class PipelineError(Document2ChunkError):
    """span 管线编排异常。"""


# 历史别名：与 MissingDependencyError 同义（① 早期命名），保留向后兼容。
OptionalDependencyError = MissingDependencyError

