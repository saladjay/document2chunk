"""structure-builder —— 章节树构建与文档组装。

职责（spec: openspec/specs/structure-builder/spec.md）：
- 从 ``ExtractionResult.content``（HeadingNode.level 已判）构建嵌套章节树。
- 消费 ``toc_entries`` 校准标题层级（信号消费）。
- ``keep_toc=True`` 时产出 ``TocNode``。
- 组装成完整 ``LogicalDocument``（section_tree / block_to_section 的唯一生产者）。

是 ``section_tree`` / ``block_to_section`` 的唯一生产者（ir-model spec §2.1）。
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from document2chunk.ir import (
    BlockNode,
    DocumentMetadata,
    ExtractionResult,
    HeadingNode,
    LogicalDocument,
    SectionNode,
    TocNode,
)


def assemble(result: ExtractionResult, *, keep_toc: bool = False) -> LogicalDocument:
    """把 ExtractionResult 组装成完整 LogicalDocument。

    1. 用 toc_entries 校准 HeadingNode.level（信号消费）。
    2. 单遍栈算法构建 section_tree + block_to_section。
    3. keep_toc 且有 toc_entries → 追加单个 TocNode 到 content。
    """
    content: List[BlockNode] = list(result.content)

    # 1. TOC 信号校准
    if result.toc_entries:
        toc_map = {
            e.text: e.level
            for e in result.toc_entries
            if e.level is not None and e.text
        }
        if toc_map:
            for block in content:
                if isinstance(block, HeadingNode) and block.text in toc_map:
                    block.level = toc_map[block.text]

    # 2. 构建章节树
    section_tree, block_to_section = build_section_tree(content)

    # 3. 可选 TocNode
    if keep_toc and result.toc_entries:
        content.append(
            TocNode(
                id="toc_000001",
                entries=[e.model_dump(exclude_none=True) for e in result.toc_entries],
            )
        )

    # 4. 递归组装附件（postprocess.split_attachments 拆出的段，designs/007 R6/009）
    attachments = [assemble(att, keep_toc=keep_toc) for att in result.attachments]

    return LogicalDocument(
        metadata=result.metadata,
        content=content,
        section_tree=section_tree,
        block_to_section=block_to_section,
        attachments=attachments,
    )


def build_section_tree(
    content: List[BlockNode],
) -> Tuple[SectionNode, Dict[str, str]]:
    """单遍栈算法：content → (section_tree 根, block_id→section_id)。"""
    root = SectionNode(id="sec_root", title="ROOT", level=0)
    stack: List[SectionNode] = [root]
    block_to_section: Dict[str, str] = {}
    counter = 0

    for block in content:
        if isinstance(block, HeadingNode):
            level = min(max(block.level, 1), 9)
            # 弹出栈中 level >= 当前的节点
            while len(stack) > 1 and stack[-1].level >= level:
                stack.pop()
            counter += 1
            section = SectionNode(
                id=f"sec_{counter:06d}",
                title=block.text,
                level=level,
                heading_node_id=block.id,
                parent_id=stack[-1].id,
            )
            stack[-1].subsections.append(section)
            stack.append(section)
        elif isinstance(block, TocNode):
            # 目录节点不参与章节树
            continue
        else:
            current = stack[-1]
            current.block_ids.append(block.id)
            block_to_section[block.id] = current.id

    return root, block_to_section
