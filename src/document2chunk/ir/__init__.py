"""规范 IR（类型化文档树）—— 所有 extractor 的统一输出契约。

设计见 ``openspec/designs/001-target-architecture.md`` §4。
"""

from document2chunk.ir.enums import BlockType, InlineType, SourceType
from document2chunk.ir.models import (
    BlockNode,
    DocumentMetadata,
    FormulaNode,
    HeadingNode,
    HyperlinkNode,
    ImageNode,
    InlineFormulaNode,
    InlineNode,
    ListItemNode,
    ListNode,
    LogicalDocument,
    ParagraphNode,
    Provenance,
    RunNode,
    RunProperties,
    SectionNode,
    TableCellNode,
    TableNode,
    TableRowNode,
    TocNode,
)
from document2chunk.ir.result import ExtractionResult, TocEntry

__all__ = [
    # enums
    "SourceType",
    "BlockType",
    "InlineType",
    # common
    "Provenance",
    "RunProperties",
    # inline
    "RunNode",
    "HyperlinkNode",
    "InlineFormulaNode",
    "InlineNode",
    # blocks
    "BlockNode",
    "HeadingNode",
    "ParagraphNode",
    "TableNode",
    "TableRowNode",
    "TableCellNode",
    "ListNode",
    "ListItemNode",
    "ImageNode",
    "FormulaNode",
    "TocNode",
    # section & document
    "SectionNode",
    "DocumentMetadata",
    "LogicalDocument",
    # 握手契约（extractor → structure-builder）
    "ExtractionResult",
    "TocEntry",
]
