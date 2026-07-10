"""Markdown 导出（遍历章节树）。"""

from __future__ import annotations

from typing import Dict, List

from document2chunk.ir import BlockNode, LogicalDocument, SectionNode

from document2chunk.export._helpers import block_markdown


def to_markdown(doc: LogicalDocument, *, include_metadata: bool = False) -> str:
    lines: List[str] = []

    if include_metadata and doc.metadata:
        m = doc.metadata
        lines.append("---")
        if m.title:
            lines.append(f"title: {m.title}")
        if m.author:
            lines.append(f"author: {m.author}")
        if m.source_file:
            lines.append(f"source: {m.source_file}")
        lines.append("---")
        lines.append("")

    index: Dict[str, BlockNode] = {b.id: b for b in doc.content}
    _emit_section(doc.section_tree, index, lines)
    return "\n".join(lines).strip() + "\n"


def _emit_section(section: SectionNode, index: Dict[str, BlockNode], lines: List[str]) -> None:
    if section.level > 0:
        lines.append(f"{'#' * min(section.level, 6)} {section.title}")
        lines.append("")

    for block_id in section.block_ids:
        block = index.get(block_id)
        if block is None:
            continue
        md = block_markdown(block)
        if md:
            lines.append(md)
            lines.append("")

    for sub in section.subsections:
        _emit_section(sub, index, lines)
