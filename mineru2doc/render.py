"""Block 列表 → 完整 Markdown 文档（spec §7）。

标题作 ``#`` 骨架，正文/表格/图/公式/列表保留。可选清理纯页码行。
"""

from __future__ import annotations

import re
from typing import List

from .model import TEXT, TABLE, IMAGE, EQUATION, LIST_T, Block

# 纯页码：1 / 12 / 12/34（MinerU content_list 偶带页码文本块）
_PAGE_NUM_RE = re.compile(r"^\d+(\s*/\s*\d+)?$")


def to_markdown(blocks: List[Block], *, clean_page_numbers: bool = True) -> str:
    """渲染为 Markdown 字符串（末尾换行）。"""
    lines: List[str] = []
    for b in blocks:
        if b.type == TEXT:
            _emit_text(b, lines, clean_page_numbers)
        elif b.type == TABLE:
            _emit_table(b, lines)
        elif b.type == IMAGE:
            _emit_image(b, lines)
        elif b.type == EQUATION:
            _emit_equation(b, lines)
        elif b.type == LIST_T:
            _emit_list(b, lines)
    return "\n".join(lines).strip() + "\n"


def _emit_text(b: Block, lines: List[str], clean_page_numbers: bool) -> None:
    if b.level is not None:  # 标题
        lines.append("#" * min(b.level, 6) + " " + (b.text or "").strip())
        lines.append("")
        return
    txt = (b.text or "").strip()
    if not txt:
        return
    if clean_page_numbers and _PAGE_NUM_RE.match(txt):
        return  # 去页码
    lines.append(txt)
    lines.append("")


def _emit_table(b: Block, lines: List[str]) -> None:
    if b.caption:
        lines.append(b.caption.strip())
        lines.append("")
    body = (b.table_body or "").strip()
    if body:
        lines.append(body)
        lines.append("")


def _emit_image(b: Block, lines: List[str]) -> None:
    cap = (b.caption or "").strip()
    path = (b.img_path or "").strip()
    lines.append(f"![{cap}]({path})")
    lines.append("")


def _emit_equation(b: Block, lines: List[str]) -> None:
    if b.latex:
        lines.append(f"$$ {b.latex.strip()} $$")
        lines.append("")


def _emit_list(b: Block, lines: List[str]) -> None:
    prefix = "1. " if b.ordered else "- "
    for it in (b.items or []):
        lines.append(prefix + it)
    lines.append("")
