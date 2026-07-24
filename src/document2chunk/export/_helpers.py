"""export 内部共享：块文本提取与 Markdown 片段生成。"""

from __future__ import annotations

import re

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


def _escape_html(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _has_merged_cells(table) -> bool:
    """表是否含合并单元格（任一格 colspan/rowspan>1）。"""
    for row in getattr(table, "rows", []) or []:
        for cell in getattr(row, "cells", []) or []:
            if getattr(cell, "colspan", 1) > 1 or getattr(cell, "rowspan", 1) > 1:
                return True
    return False


def block_markdown(block: BlockNode) -> str:
    """单个块 → Markdown 片段。"""
    if isinstance(block, HeadingNode):
        return f"{'#' * min(block.level, 6)} {block.text}"
    if isinstance(block, ParagraphNode):
        return _escape_md(block.text)
    if isinstance(block, TableNode):
        # designs/009：表格三分流——
        #   ① 挂 table_image_id（复杂表 + image 模式）→ 图片；
        #   ② 含合并格的复杂表（默认 html 模式，未截图）→ HTML <table>（保留 colspan/rowspan）；
        #   ③ 简单表（全 1×1）→ markdown 管道表格。
        # 结构数据始终在 IR（JSON/检索可用）。
        img_id = getattr(block, "table_image_id", None)
        if img_id:
            alt = (block.metadata or {}).get("caption") or "表格"
            return f"![{alt}]({img_id})"
        if _has_merged_cells(block):
            return html_table_markdown(block)
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
    # 注：colspan/rowspan 在 Markdown 管道表格中无法表达；复杂表用 html_table_markdown。


def html_table_markdown(table: TableNode) -> str:
    """TableNode → HTML ``<table>``（保留 colspan/rowspan），写进 markdown。

    markdown 管道表格不支持合并单元格；复杂表用 HTML 表格（GitHub/VS Code/pandoc 等多数
    渲染器支持）。全程文字、可检索。
    """
    if not table.rows:
        return ""
    out = ["<table>"]
    for row in table.rows:
        out.append("<tr>")
        for c in row.cells:
            tag = "th" if row.is_header else "td"
            attrs = ""
            if getattr(c, "colspan", 1) > 1:
                attrs += f' colspan="{c.colspan}"'
            if getattr(c, "rowspan", 1) > 1:
                attrs += f' rowspan="{c.rowspan}"'
            txt = _escape_html(cell_text(c))
            out.append(f"<{tag}{attrs}>{txt}</{tag}>")
        out.append("</tr>")
    out.append("</table>")
    return "\n".join(out)


def list_markdown(lst: ListNode) -> str:
    lines = []
    for n, item in enumerate(lst.items, 1):
        indent = "  " * item.level
        text = " ".join(block_text(b) for b in item.blocks).strip()
        if lst.ordered:
            # 保留原序号：文本已含数字标记（OCR "1. xxx"）→ 原样；否则 GFM 序号
            if re.match(r"^\d+[.、)]", text):
                lines.append(f"{indent}{text}")
            else:
                lines.append(f"{indent}{n}. {text}")
        else:
            lines.append(f"{indent}- {text}")
    return "\n".join(lines)
