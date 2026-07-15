"""方案 A 的映射层：markdown 元素 + parsing_res_list(bbox) + images → BlockNode 列表。

- markdown 元素建结构/层级（来自 _markdown.parse_markdown）。
- parsing_res_list 按 order 关联补 bbox（丢弃 page_number/header/footer/number 后 1:1）。
- HTML <table> → TableNode（lxml）；图片 base64 → 落盘。
"""

from __future__ import annotations

import base64
import os
from typing import Any, Dict, List, Optional, Tuple

from lxml import html as lxml_html

from document2chunk.extractors.ocr._markdown import parse_markdown
from document2chunk.ir import (
    BlockNode,
    FormulaNode,
    HeadingNode,
    ImageNode,
    ListItemNode,
    ListNode,
    ParagraphNode,
    Provenance,
    SourceType,
    TableCellNode,
    TableRowNode,
    TableNode,
)

DROP_LABELS = {"page_number", "header", "footer", "number"}


class _Idc:
    """跨页共享的 ID 计数器。"""

    def __init__(self) -> None:
        self.b = self.r = self.c = self.cell = 0

    def block(self) -> str:
        self.b += 1
        return f"block_{self.b:06d}"

    def run(self) -> str:
        self.r += 1
        return f"run_{self.r:06d}"

    def row(self) -> str:
        self.r += 1
        return f"row_{self.r:06d}"

    def cell_id(self) -> str:
        self.cell += 1
        return f"cell_{self.cell:06d}"


def build_page_blocks(
    markdown: str,
    parsing_res_list: List[Dict[str, Any]],
    images: Dict[str, str],
    page_index: int,
    idc: _Idc,
    image_out_dir: Optional[str],
    extract_images: bool,
    _img_counter: List[int],
) -> List[BlockNode]:
    """单页 markdown+parsing_res_list → BlockNode 列表（带 provenance）。"""
    elements = parse_markdown(markdown)
    content_blocks = [b for b in (parsing_res_list or []) if b.get("block_label") not in DROP_LABELS]

    out: List[BlockNode] = []
    for i, el in enumerate(elements):
        bbox = content_blocks[i].get("block_bbox") if i < len(content_blocks) else None
        prov = Provenance(source_type=SourceType.OCR, page_index=page_index, bbox=bbox)
        node = _element_to_node(el, images, page_index, idc, image_out_dir, extract_images, _img_counter, prov)
        if node is not None:
            out.append(node)
    return out


def _element_to_node(
    el: Dict[str, Any],
    images: Dict[str, str],
    page_index: int,
    idc: _Idc,
    image_out_dir: Optional[str],
    extract_images: bool,
    _img_counter: List[int],
    prov: Provenance,
) -> Optional[BlockNode]:
    kind = el["kind"]

    if kind == "heading":
        return HeadingNode(
            id=idc.block(),
            level=min(max(int(el["level"]), 1), 9),
            text=el["text"],
            provenance=prov,
        )

    if kind == "paragraph":
        return ParagraphNode(id=idc.block(), text=el["text"], provenance=prov)

    if kind == "formula":
        return FormulaNode(id=idc.block(), latex=el.get("latex"), provenance=prov)

    if kind == "table":
        return _html_table_to_node(el["html"], idc, prov)

    if kind == "image":
        return _image_to_node(el, images, page_index, idc, image_out_dir, extract_images, _img_counter, prov)

    if kind == "list":
        items = [
            ListItemNode(
                id=idc.cell_id(),
                level=0,
                blocks=[ParagraphNode(id=idc.block(), text=t)],
            )
            for t in el["items"]
        ]
        return ListNode(id=idc.block(), ordered=bool(el["ordered"]), items=items, provenance=prov)

    # 兜底
    return ParagraphNode(id=idc.block(), text=str(el), provenance=prov)


def _html_table_to_node(html_str: str, idc: _Idc, prov: Provenance) -> TableNode:
    """HTML <table> → TableNode（lxml 解析行/单元格，保留 colspan/rowspan）。"""
    rows: List[TableRowNode] = []
    try:
        frag = lxml_html.fromstring(html_str)
        trs = frag.xpath(".//tr")
    except Exception:
        trs = []

    for ri, tr in enumerate(trs):
        cells: List[TableCellNode] = []
        for tc in tr.xpath("./td | ./th"):
            text = (tc.text_content() or "").strip()
            try:
                colspan = int(tc.get("colspan", "1") or "1")
            except ValueError:
                colspan = 1
            try:
                rowspan = int(tc.get("rowspan", "1") or "1")
            except ValueError:
                rowspan = 1
            cells.append(
                TableCellNode(
                    id=idc.cell_id(),
                    blocks=[ParagraphNode(id=idc.block(), text=text)],
                    colspan=colspan,
                    rowspan=rowspan,
                )
            )
        rows.append(TableRowNode(id=idc.row(), cells=cells, is_header=(ri == 0)))
    return TableNode(id=idc.block(), rows=rows, provenance=prov)


def _image_to_node(
    el: Dict[str, Any],
    images: Dict[str, str],
    page_index: int,
    idc: _Idc,
    image_out_dir: Optional[str],
    extract_images: bool,
    _img_counter: List[int],
    prov: Provenance,
) -> ImageNode:
    ref = el.get("ref", "")
    fmt = ref.rsplit(".", 1)[-1].lower() if "." in ref else "png"
    _img_counter[0] += 1
    filename = f"p{page_index}_{_img_counter[0]}.{fmt}"

    if extract_images and image_out_dir:
        b64 = images.get(ref)
        if b64:
            try:
                os.makedirs(image_out_dir, exist_ok=True)
                with open(os.path.join(image_out_dir, filename), "wb") as f:
                    f.write(base64.b64decode(b64))
            except Exception:
                pass  # 落盘失败不阻断

    return ImageNode(
        id=idc.block(),
        image_id=filename,
        format=fmt,
        alt=el.get("alt") or None,
        provenance=prov,
        metadata={"source_ref": ref} if ref else {},
    )
