"""规范 IR 节点模型（pydantic v2）。

约定：
- 块节点 / 行内节点均为判别联合（以 ``type`` Literal 判别）。
- ``provenance`` 可选：PDF/OCR 节点携带（page_index/bbox/confidence）；docx 节点不携带。
- ``BlockNode`` 可递归嵌套（表格单元格、列表项内可含任意块）。
- ID 在单文档内唯一稳定：``block_000001`` / ``sec_000001`` / ``run_000001``。

设计依据：``openspec/designs/001-target-architecture.md`` §4。
"""

from __future__ import annotations

from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from document2chunk.ir.enums import SourceType

# ============================================================
# 出处（可选）与样式
# ============================================================


class Provenance(BaseModel):
    """节点出处。PDF/OCR 携带；docx 默认不携带（designs/001 D6）。"""

    source_type: SourceType
    page_index: Optional[int] = Field(default=None, ge=0)  # 0-based
    bbox: Optional[List[float]] = None  # [x0, y0, x1, y1]
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)  # OCR


class RunProperties(BaseModel):
    """字符样式。docx 经样式继承链解析；PDF 来自 span flags；OCR 多为未知。"""

    font: Optional[str] = None
    font_size: Optional[float] = Field(default=None, gt=0.0)  # pt
    bold: Optional[bool] = None
    italic: Optional[bool] = None
    underline: Optional[bool] = None
    strikethrough: Optional[bool] = None
    color: Optional[str] = None  # hex，如 "#FF0000"
    highlight: Optional[str] = None
    is_superscript: Optional[bool] = None
    is_subscript: Optional[bool] = None


# ============================================================
# 行内节点
# ============================================================


class _InlineBase(BaseModel):
    id: str
    provenance: Optional[Provenance] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RunNode(_InlineBase):
    """文本运行片段。PDF 的 span 与 docx 的 <w:r> 都映射到此处。"""

    type: Literal["run"] = "run"
    text: str = ""
    style: Optional[RunProperties] = None


class HyperlinkNode(_InlineBase):
    type: Literal["hyperlink"] = "hyperlink"
    target: str
    runs: List[RunNode] = Field(default_factory=list)


class InlineFormulaNode(_InlineBase):
    """行内公式（LaTeX）。块级公式用 :class:`FormulaNode`。F18。"""

    type: Literal["inline_formula"] = "inline_formula"
    latex: str = ""


InlineNode = Annotated[
    Union[RunNode, HyperlinkNode, InlineFormulaNode], Field(discriminator="type")
]


# ============================================================
# 块节点
# ============================================================


class _BlockBase(BaseModel):
    model_config = ConfigDict(extra="allow")  # 容许源特有字段透传

    id: str
    provenance: Optional[Provenance] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class HeadingNode(_BlockBase):
    type: Literal["heading"] = "heading"
    level: int = Field(..., ge=1, le=9)  # H1–9
    text: str
    runs: List[RunNode] = Field(default_factory=list)


class ParagraphNode(_BlockBase):
    type: Literal["paragraph"] = "paragraph"
    runs: List[InlineNode] = Field(default_factory=list)  # RunNode / HyperlinkNode
    text: str = ""  # 便捷纯文本（拼接 runs）


class FormulaNode(_BlockBase):
    type: Literal["formula"] = "formula"
    latex: Optional[str] = None
    text: Optional[str] = None


class ImageNode(_BlockBase):
    type: Literal["image"] = "image"
    image_id: str
    format: Optional[str] = None  # png/jpeg/svg/emf
    width_emu: Optional[int] = Field(default=None, ge=0)
    height_emu: Optional[int] = Field(default=None, ge=0)
    alt: Optional[str] = None
    data: Optional[bytes] = None  # 可选二进制；JSON 输出通常 exclude


class TocNode(_BlockBase):
    """可选导出的目录（designs/001 D7）。默认不进 content。"""

    type: Literal["toc"] = "toc"
    entries: List[Dict[str, Any]] = Field(default_factory=list)  # [{text, level, page?}]


# ----- 表格（递归嵌套块）-----


class TableCellNode(BaseModel):
    id: str
    blocks: List[BlockNode] = Field(default_factory=list)  # 可嵌套段落/列表/子表格
    colspan: int = Field(default=1, ge=1)
    rowspan: int = Field(default=1, ge=1)


class TableRowNode(BaseModel):
    id: str
    cells: List[TableCellNode] = Field(default_factory=list)
    is_header: bool = False


class TableNode(_BlockBase):
    type: Literal["table"] = "table"
    rows: List[TableRowNode] = Field(default_factory=list)


# ----- 列表（递归嵌套块）-----


class ListItemNode(BaseModel):
    id: str
    level: int = Field(default=0, ge=0)  # 多级列表
    blocks: List[BlockNode] = Field(default_factory=list)


class ListNode(_BlockBase):
    type: Literal["list"] = "list"
    ordered: bool = False
    items: List[ListItemNode] = Field(default_factory=list)


BlockNode = Annotated[
    Union[
        HeadingNode,
        ParagraphNode,
        TableNode,
        ListNode,
        ImageNode,
        FormulaNode,
        TocNode,
    ],
    Field(discriminator="type"),
]


# ============================================================
# 章节节点
# ============================================================


class SectionNode(BaseModel):
    id: str
    title: str
    level: int = Field(..., ge=0, le=9)  # 0 = 根
    heading_node_id: Optional[str] = None
    block_ids: List[str] = Field(default_factory=list)
    subsections: List[SectionNode] = Field(default_factory=list)  # 嵌套子章节（自包含）
    parent_id: Optional[str] = None


# ============================================================
# 文档
# ============================================================


class DocumentMetadata(BaseModel):
    title: Optional[str] = None
    author: Optional[str] = None
    source_type: Optional[SourceType] = None
    source_file: Optional[str] = None
    created: Optional[str] = None
    modified: Optional[str] = None
    page_count: Optional[int] = Field(default=None, ge=0)  # 仅 PDF/OCR 有意义
    generator: Optional[str] = None
    custom: Dict[str, Any] = Field(default_factory=dict)


class LogicalDocument(BaseModel):
    """规范 IR —— 所有 extractor 的统一输出。"""

    metadata: DocumentMetadata
    content: List[BlockNode] = Field(default_factory=list)  # 扁平阅读序列
    section_tree: SectionNode  # 章节层级（根 level=0）
    block_to_section: Dict[str, str] = Field(default_factory=dict)  # block_id → section_id

    def iter_blocks(self):
        """深度优先遍历所有块节点（含表格单元格、列表项内的嵌套块）。"""

        def _walk(block: BaseModel):
            yield block
            if isinstance(block, TableNode):
                for row in block.rows:
                    for cell in row.cells:
                        for inner in cell.blocks:
                            yield from _walk(inner)
            elif isinstance(block, ListNode):
                for item in block.items:
                    for inner in item.blocks:
                        yield from _walk(inner)

        for block in self.content:
            yield from _walk(block)

    def get_block(self, block_id: str) -> Optional[BaseModel]:
        """根据 ID 查找块节点（深度遍历，含嵌套块）。"""
        for block in self.iter_blocks():
            if block.id == block_id:
                return block
        return None

    def iter_sections(self):
        """深度优先遍历所有章节节点。"""

        def _walk(node: SectionNode):
            yield node
            for child in node.subsections:
                yield from _walk(child)

        yield from _walk(self.section_tree)

    def get_section(self, section_id: str) -> Optional[SectionNode]:
        """根据 ID 查找章节节点。"""
        for node in self.iter_sections():
            if node.id == section_id:
                return node
        return None


# ============================================================
# 解析前向引用（递归嵌套：表格/列表 → BlockNode）
# ============================================================
for _model in (
    SectionNode,
    TableCellNode,
    TableRowNode,
    TableNode,
    ListItemNode,
    ListNode,
    HeadingNode,
    ParagraphNode,
    ImageNode,
    FormulaNode,
    TocNode,
    RunNode,
    HyperlinkNode,
    InlineFormulaNode,
    LogicalDocument,
):
    _model.model_rebuild()
