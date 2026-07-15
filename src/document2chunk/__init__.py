"""document2chunk — multi-format document parser for RAG.

规范 IR（类型化文档树）定义在 :mod:`document2chunk.ir`。所有格式 extractor
统一输出 :class:`document2chunk.ir.LogicalDocument`，经 :func:`parse` 统一入口获取。
"""

from document2chunk.exceptions import (
    Document2ChunkError,
    InvalidSourceError,
    MissingDependencyError,
    UnsupportedFormatError,
)
from document2chunk.ir import LogicalDocument

__version__ = "0.1.0"

__all__ = [
    "LogicalDocument",
    "parse",
    "Document2ChunkError",
    "UnsupportedFormatError",
    "MissingDependencyError",
    "InvalidSourceError",
    "__version__",
]


def __getattr__(name):
    # parse 走惰性导入：保持 ``import document2chunk`` 轻量，并避免与 api.py 的
    # 循环导入（api.py 顶层 ``from document2chunk import __version__``）。
    if name == "parse":
        from document2chunk.api import parse as _parse

        return _parse
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
