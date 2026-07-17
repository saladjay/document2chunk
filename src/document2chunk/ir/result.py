"""提取结果握手类型（extractor → structure-builder 的交接契约）。

为让各格式 extractor 与 structure-builder **完全解耦**（都只依赖 ir-model），
extractor 不直接产出完整 ``LogicalDocument``，而是产出 ``ExtractionResult``，
由编排层（api）调用 ``structure.assemble`` 组装成 ``LogicalDocument``。

加性扩展：不修改任何现有节点定义。
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from document2chunk.ir.models import BlockNode, DocumentMetadata


class TocEntry(BaseModel):
    """单条目录条目（作信号消费 + 可选 TocNode 导出）。"""

    text: str
    level: Optional[int] = Field(default=None, ge=1, le=9)
    page: Optional[int] = Field(default=None, ge=0)


class ExtractionResult(BaseModel):
    """extractor 的统一产出。

    Attributes:
        content: 已判定 heading level 的扁平块序列（阅读顺序）。
        metadata: 文档元数据（source_type 已设定）。
        toc_entries: 可选目录条目（作 structure-builder 校准标题层级/导出 TocNode 的信号）。
    """

    content: List[BlockNode] = Field(default_factory=list)
    metadata: DocumentMetadata
    toc_entries: Optional[List[TocEntry]] = None
    attachments: List["ExtractionResult"] = Field(default_factory=list)  # 拆分的附件（designs/007 R6）


ExtractionResult.model_rebuild()
