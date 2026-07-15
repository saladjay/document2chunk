"""markdown → IR 块解析器（共享，D11）。

把 GFM markdown 解析为 ir-model 块节点序列，供 ocr-extractor（远程 PaddleOCR 服务
返回 markdown）及未来 markdown/html-extractor 复用。映射见
``openspec/specs/ocr-extractor/spec.md`` §5。

支持的块：
- ATX 标题 ``#..######`` → :class:`HeadingNode`
- GFM 管道表格 → :class:`TableNode`（首行表头）
- 列表 ``-`` / ``*`` / ``+`` / ``1.`` （缩进多级）→ :class:`ListNode`
- 图片行 ``![alt](ref)`` → :class:`ImageNode`
- 块公式 ``$$..$$`` → :class:`FormulaNode`
- 其余 → :class:`ParagraphNode`

provenance 默认 None（按流式文档处理，同 docx；bbox 由调用方按需补充）。
"""

from __future__ import annotations

import re
from typing import List, Optional

from document2chunk.ir import (
    BlockNode,
    FormulaNode,
    HeadingNode,
    ImageNode,
    ListItemNode,
    ListNode,
    ParagraphNode,
    RunNode,
    SourceType,
    TableCellNode,
    TableRowNode,
    TableNode,
)

# ATX 标题：## 标题 ##（尾部 # 可选）
_ATX_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$")

# 列表项：<缩进><标记><空格><内容>；标记分组1=无序(-*+)，分组2=有序(数字.)
_LIST_RE = re.compile(r"^(\s*)(?:([-*+])|(\d+)[.)])\s+(.*)$")

# 独占一行的图片 ![alt](ref)
_IMAGE_LINE_RE = re.compile(r"^!\[([^\]]*)\]\(([^)]+)\)\s*$")

# 内联图片/链接用于段落内提取图片引用
_INLINE_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")

# 主题分隔线 --- / *** / ___
_THEMATIC_RE = re.compile(r"^(?:-{3,}|\*{3,}|_{3,})\s*$")

# 表格分隔行单元格：:?-+:?（GFM 允许单短横，如 | - | - |）
_SEP_CELL_RE = re.compile(r":?-+:?")


class _IdGen:
    """单文档内稳定 ID 生成器（6 位补零，1-based）。"""

    def __init__(self) -> None:
        self._block = 0
        self._run = 0
        self._row = 0
        self._cell = 0

    def block(self) -> str:
        self._block += 1
        return f"block_{self._block:06d}"

    def run(self) -> str:
        self._run += 1
        return f"run_{self._run:06d}"

    def row(self) -> str:
        self._row += 1
        return f"row_{self._row:06d}"

    def cell(self) -> str:
        self._cell += 1
        return f"cell_{self._cell:06d}"


def _clean_inline(text: str) -> str:
    """剥离常见内联标记（``**`` 加粗 / ``*`` 斜体 / `` ` `` 代码 / ``~~`` 删除线），保留文字。"""
    if not text:
        return text
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)  # **bold**
    text = re.sub(r"(?<!\*)\*([^*]+?)\*(?!\*)", r"\1", text)  # *italic*
    text = re.sub(r"~~(.+?)~~", r"\1", text)  # ~~del~~
    text = re.sub(r"`([^`]+?)`", r"\1", text)  # `code`
    return text


def _run_of(text: str, idgen: _IdGen) -> RunNode:
    return RunNode(id=idgen.run(), text=text)


def _is_table_sep(line: str) -> bool:
    """是否为表格分隔行（``| --- | :---: | --- |``）。"""
    s = line.strip()
    if "|" not in s:
        return False
    inner = s.strip("|")
    cells = [c.strip() for c in inner.split("|")]
    if not cells or not all(cells):
        return False
    return all(_SEP_CELL_RE.fullmatch(c) for c in cells if c != "")


def _split_table_row(line: str) -> List[str]:
    """拆分表格行为单元格（处理外层 | 与转义 \\| 的简单情形）。"""
    s = line.strip()
    # 统一转义竖线占位，避免被切
    s = s.replace("\\|", "\x00")
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [_clean_inline(c.replace("\x00", "|").strip()) for c in s.split("|")]


# ============================================================
# 主解析
# ============================================================


def markdown_to_blocks(
    markdown: str,
    *,
    source_type: SourceType = SourceType.OCR,
    idgen: Optional[_IdGen] = None,
) -> List[BlockNode]:
    """把 markdown 文本解析为 IR 块序列。

    Args:
        markdown: GFM markdown 文本。
        source_type: 节点来源类型（仅用于语义，provenance 默认 None）。
        idgen: 可选 ID 生成器（None 则内部新建，单文档 1-based）。
    """
    idgen = idgen or _IdGen()
    lines = markdown.splitlines()
    blocks: List[BlockNode] = []
    i = 0
    n = len(lines)

    while i < n:
        raw = lines[i]
        stripped = raw.strip()

        # 空行
        if not stripped:
            i += 1
            continue

        # 主题分隔线
        if _THEMATIC_RE.match(stripped):
            i += 1
            continue

        # ATX 标题
        m = _ATX_RE.match(stripped)
        if m:
            level = len(m.group(1))
            text = _clean_inline(m.group(2).strip())
            blocks.append(
                HeadingNode(
                    id=idgen.block(),
                    level=level,
                    text=text,
                    runs=[_run_of(text, idgen)],
                )
            )
            i += 1
            continue

        # 围栏代码块 ```lang .. ```
        if stripped.startswith("```") or stripped.startswith("~~~"):
            i = _consume_fenced(lines, i, idgen, blocks)
            continue

        # 块公式 $$ ... $$（可在同行闭合或多行）
        if stripped.startswith("$$"):
            i = _consume_formula(lines, i, idgen, blocks)
            continue

        # GFM 管道表格：当前行含 | 且下一行是分隔行
        if "|" in stripped and i + 1 < n and _is_table_sep(lines[i + 1]):
            i = _consume_table(lines, i, idgen, blocks)
            continue

        # 列表
        if _LIST_RE.match(raw):
            i = _consume_list(lines, i, idgen, blocks)
            continue

        # 独占图片行
        mim = _IMAGE_LINE_RE.match(stripped)
        if mim:
            blocks.append(
                ImageNode(
                    id=idgen.block(),
                    image_id=mim.group(2).strip(),
                    alt=mim.group(1).strip() or None,
                )
            )
            i += 1
            continue

        # 段落：收集连续的非块行
        i = _consume_paragraph(lines, i, idgen, blocks)

    return blocks


# ============================================================
# 块消费器
# ============================================================


def _consume_fenced(lines, i, idgen, blocks) -> int:
    """围栏代码块 → ParagraphNode（保留代码文本）。"""
    fence = lines[i].strip()[:3]
    lang = lines[i].strip()[3:].strip()
    i += 1
    code_lines: List[str] = []
    while i < len(lines) and not lines[i].strip().startswith(fence):
        code_lines.append(lines[i])
        i += 1
    if i < len(lines):  # 跳过闭合围栏
        i += 1
    text = "\n".join(code_lines).strip()
    label = f"`{lang}`\n{text}" if lang else text
    blocks.append(ParagraphNode(id=idgen.block(), text=label, runs=[_run_of(label, idgen)]))
    return i


def _consume_formula(lines, i, idgen, blocks) -> int:
    """块公式 $$..$$ → FormulaNode(latex)。"""
    first = lines[i].strip()
    # 同行闭合：$$ latex $$
    if first.endswith("$$") and len(first) > 2:
        latex = first[2:-2].strip()
        i += 1
    else:
        # 多行：收集到下一个 $$
        i += 1
        buf: List[str] = []
        while i < len(lines) and not lines[i].strip().endswith("$$") and lines[i].strip() != "$$":
            buf.append(lines[i])
            i += 1
        if i < len(lines):  # 闭合行
            closing = lines[i].strip()
            if closing != "$$":
                buf.append(closing[:-2])  # 去掉尾部 $$
            i += 1
        latex = "\n".join(buf).strip()
    blocks.append(FormulaNode(id=idgen.block(), latex=latex or None, text=latex or None))
    return i


def _consume_table(lines, i, idgen, blocks) -> int:
    """GFM 管道表格 → TableNode（首行表头）。"""
    header = _split_table_row(lines[i])
    i += 2  # 跳过表头 + 分隔行
    rows: List[List[str]] = [header]
    while i < len(lines):
        s = lines[i].strip()
        if not s or "|" not in s:
            break
        rows.append(_split_table_row(lines[i]))
        i += 1

    table_rows = []
    for r, row in enumerate(rows):
        cells = [
            TableCellNode(
                id=idgen.cell(),
                blocks=[ParagraphNode(id=idgen.block(), text=(cell or "").strip())],
            )
            for cell in row
        ]
        table_rows.append(TableRowNode(id=idgen.row(), is_header=(r == 0), cells=cells))
    blocks.append(TableNode(id=idgen.block(), rows=table_rows))
    return i


def _consume_list(lines, i, idgen, blocks) -> int:
    """连续列表项 → ListNode（ordered 取首项标记；缩进→level）。"""
    items: List[ListItemNode] = []
    ordered = False
    first = True
    while i < len(lines):
        raw = lines[i]
        if not raw.strip():
            # 空行后若仍是列表项则继续，否则结束
            if i + 1 < len(lines) and _LIST_RE.match(lines[i + 1]):
                i += 1
                continue
            break
        m = _LIST_RE.match(raw)
        if not m:
            break
        indent = len(m.group(1))
        level = min(indent // 2, 8)
        content = _clean_inline(m.group(4).strip())
        is_ordered = bool(m.group(3))
        if first:
            ordered = is_ordered
            first = False
        items.append(
            ListItemNode(
                id=idgen.block(),
                level=level,
                blocks=[ParagraphNode(id=idgen.block(), text=content)],
            )
        )
        i += 1
    if items:
        blocks.append(ListNode(id=idgen.block(), ordered=ordered, items=items))
    return i


def _consume_paragraph(lines, i, idgen, blocks) -> int:
    """收集连续非块行 → ParagraphNode（内联图片抽为独立 ImageNode 后置）。"""
    buf: List[str] = []
    trailing_images: List[BlockNode] = []
    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()
        if not stripped:
            break
        # 遇到块起始则止
        if (
            _ATX_RE.match(stripped)
            or _THEMATIC_RE.match(stripped)
            or stripped.startswith("```")
            or stripped.startswith("~~~")
            or stripped.startswith("$$")
            or _LIST_RE.match(raw)
            or (_IMAGE_LINE_RE.match(stripped))
            or ("|" in stripped and i + 1 < len(lines) and _is_table_sep(lines[i + 1]))
        ):
            break
        # 段落内联图片 → 抽出为独立 ImageNode（附在段落后）
        if _INLINE_IMAGE_RE.search(stripped) and _IMAGE_LINE_RE.match(stripped):
            break
        buf.append(stripped)
        i += 1

    if buf:
        text = _clean_inline(" ".join(buf))
        blocks.append(ParagraphNode(id=idgen.block(), text=text, runs=[_run_of(text, idgen)]))
    blocks.extend(trailing_images)
    return i
