"""export 内部共享：块文本提取与 Markdown 片段生成。"""

from __future__ import annotations

from document2chunk.ir import (
    BlockNode,
    FormulaNode,
    HeadingNode,
    HyperlinkNode,
    ImageNode,
    ListNode,
    ParagraphNode,
    TableNode,
    TocNode,
)


def block_text(block: BlockNode) -> str:
    """块的纯文本（用于 plain_text / 表格单元格 / 列表项）。"""
    if isinstance(block, (HeadingNode, ParagraphNode)):
        if block.text:
            return block.text
        return "".join(_run_text(r) for r in getattr(block, "runs", []))
    if isinstance(block, TableNode):
        return "\n".join(
            "\t".join(cell_text(c) for c in row.cells) for row in block.rows
        )
    if isinstance(block, ListNode):
        return "\n".join(
            " ".join(block_text(b) for b in item.blocks) for item in block.items
        )
    if isinstance(block, ImageNode):
        return block.alt or ""
    if isinstance(block, FormulaNode):
        return block.text or block.latex or ""
    return ""


def _run_text(run) -> str:
    if isinstance(run, HyperlinkNode):
        return "".join(r.text for r in run.runs)
    return getattr(run, "text", "") or ""


def cell_text(cell) -> str:
    return " ".join(block_text(b) for b in cell.blocks).strip()


def _escape_md(text: str) -> str:
    """转义 GFM 特殊字符（乘号 * 等避免被当 emphasis）。"""
    return (text or "").replace("*", "\\*")


def block_markdown(block: BlockNode) -> str:
    """单个块 → Markdown 片段。"""
    if isinstance(block, HeadingNode):
        return f"{'#' * min(block.level, 6)} {block.text}"
    if isinstance(block, ParagraphNode):
        return _escape_md(block.text)
    if isinstance(block, TableNode):
        return table_markdown(block)
    if isinstance(block, ListNode):
        return list_markdown(block)
    if isinstance(block, ImageNode):
        alt = block.alt or block.image_id
        return f"![{alt}]({block.image_id})"
    if isinstance(block, FormulaNode):
        return f"${block.latex}$" if block.latex else (block.text or "")
    if isinstance(block, TocNode):
        return "\n".join(f"- {e.get('text', '')}" for e in block.entries)
    return block_text(block)


def table_markdown(table: TableNode) -> str:
    if not table.rows:
        return ""
    lines = []
    for i, row in enumerate(table.rows):
        cells = [cell_text(c).replace("|", "\\|") or " " for c in row.cells]
        lines.append("| " + " | ".join(cells) + " |")
        if i == 0:
            lines.append("| " + " | ".join("---" for _ in row.cells) + " |")
    return "\n".join(lines)
    # 注：colspan/rowspan 在 Markdown 表格中无法表达，按平铺处理。


def list_markdown(lst: ListNode) -> str:
    lines = []
    for item in lst.items:
        indent = "  " * item.level
        marker = "1. " if lst.ordered else "- "
        text = " ".join(block_text(b) for b in item.blocks).strip()
        lines.append(f"{indent}{marker}{text}")
    return "\n".join(lines)
