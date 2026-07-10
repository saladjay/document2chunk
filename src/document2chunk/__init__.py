"""document2chunk — multi-format document parser for RAG.

规范 IR（类型化文档树）定义在 :mod:`document2chunk.ir`。所有格式 extractor
统一输出 :class:`document2chunk.ir.LogicalDocument`。
"""

from document2chunk.ir import LogicalDocument

__version__ = "0.1.0"

__all__ = ["LogicalDocument", "__version__"]
