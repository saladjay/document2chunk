"""HTML ``<table>`` → IR TableNode（designs/008 §4）。

用 stdlib :mod:`html.parser`（零依赖）解析服务返回的表格 HTML，**保留 colspan/rowspan**
（现有 ``_mapping._table_to_table_node`` 全填 1、丢合并信息；本解析器按属性如实填）。

服务 HTML 形如：
    <table><tbody>
      <tr><td colspan="2">合并表头</td><td>列C</td></tr>
      <tr><td rowspan="2">a</td><td>b</td><td>c</td></tr>
      <tr><td>b2</td><td>c2</td></tr>
    </tbody></table>
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Optional

from document2chunk.ir import (
    ParagraphNode,
    Provenance,
    RunNode,
    SourceType,
    TableCellNode,
    TableNode,
    TableRowNode,
)

_CELL_TAGS = ("td", "th")
_WS = re.compile(r"\s+")


class _Idc:
    """单表内 ID 生成器（6 位补零，1-based）。"""

    def __init__(self) -> None:
        self.b = self.r = self.c = 0

    def block(self) -> str:
        self.b += 1
        return f"block_{self.b:06d}"

    def row(self) -> str:
        self.r += 1
        return f"row_{self.r:06d}"

    def cell(self) -> str:
        self.c += 1
        return f"cell_{self.c:06d}"


class _TableHTMLParser(HTMLParser):
    """收集 rows: [[{text, colspan, rowspan, is_th}, ...], ...]。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[dict]] = []
        self._row: Optional[list[dict]] = None
        self._cell: Optional[dict] = None
        self._buf: list[str] = []
        self._in_table = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table":
            self._in_table = True
        elif tag == "tr" and self._in_table:
            self._row = []
        elif tag in _CELL_TAGS and self._row is not None:
            self._cell = {
                "colspan": int(a.get("colspan", 1) or 1),
                "rowspan": int(a.get("rowspan", 1) or 1),
                "is_th": tag == "th",
            }
            self._buf = []

    def handle_data(self, data):
        if self._cell is not None:
            self._buf.append(data)

    def handle_endtag(self, tag):
        if tag in _CELL_TAGS and self._cell is not None:
            text = _WS.sub(" ", "".join(self._buf)).strip()
            self._cell["text"] = text
            self._row.append(self._cell)
            self._cell = None
            self._buf = []
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None
        elif tag == "table":
            self._in_table = False


def html_to_table_node(
    html: str,
    *,
    page_index: Optional[int] = None,
    source_type: SourceType = SourceType.OCR,
    table_bbox: Optional[list[float]] = None,
    cell_boxes: Optional[list[list[float]]] = None,
    idc: Optional[_Idc] = None,
    header_first_row: bool = True,
) -> TableNode:
    """服务返回的表格 HTML → :class:`TableNode`（保留 colspan/rowspan）。

    Args:
        html: ``<table>...</table>`` 片段（服务 ``tables[].html``）。
        page_index: 表格所在页（→ ``provenance.page_index``）。
        table_bbox: 整表 bbox（→ ``TableNode.provenance.bbox``）。
        cell_boxes: 每个单元格的 bbox（按行优先顺序对齐 → 内层段落 provenance.bbox）。
        header_first_row: 无 ``<th>`` 时是否把首行视为表头。
    """
    idc = idc or _Idc()
    p = _TableHTMLParser()
    p.feed(html or "")
    rows_raw = p.rows

    has_th = any(c["is_th"] for r in rows_raw for c in r)
    rows: list[TableRowNode] = []
    cell_idx = 0
    for ri, r in enumerate(rows_raw):
        cells = []
        for c in r:
            text = c.get("text", "")
            cell_box = None
            if cell_boxes and cell_idx < len(cell_boxes):
                cell_box = cell_boxes[cell_idx]
            cell_idx += 1
            cells.append(
                TableCellNode(
                    id=idc.cell(),
                    colspan=c.get("colspan", 1),
                    rowspan=c.get("rowspan", 1),
                    blocks=[
                        ParagraphNode(
                            id=idc.block(),
                            text=text,
                            runs=[RunNode(id=idc.block(), text=text)],
                            provenance=Provenance(
                                source_type=source_type, page_index=page_index, bbox=cell_box
                            )
                            if cell_box
                            else None,
                        )
                    ],
                )
            )
        is_header = (not has_th and ri == 0 and header_first_row) or all(c["is_th"] for c in r)
        rows.append(TableRowNode(id=idc.row(), cells=cells, is_header=bool(is_header)))

    return TableNode(
        id=idc.block(),
        rows=rows,
        provenance=Provenance(
            source_type=source_type, page_index=page_index, bbox=table_bbox
        ),
    )
