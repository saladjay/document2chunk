"""方案 A 的映射层：markdown 元素 + parsing_res_list(bbox) + images → BlockNode 列表。

- markdown 元素建结构/层级（来自 _markdown.parse_markdown）。
- parsing_res_list 按 order 关联补 bbox（丢弃 page_number/header/footer/number 后 1:1）。
- HTML <table> → TableNode（lxml）；图片 base64 → 落盘。
"""

from __future__ import annotations

import base64
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from lxml import html as lxml_html

from document2chunk.extractors.ocr._markdown import parse_markdown
from document2chunk.ir import (
    BlockNode,
    FormulaNode,
    HeadingNode,
    ImageNode,
    InlineFormulaNode,
    ListItemNode,
    ListNode,
    ParagraphNode,
    Provenance,
    RunNode,
    SourceType,
    TableCellNode,
    TableRowNode,
    TableNode,
)

DROP_LABELS = {"page_number", "header", "footer", "number"}


def _convert_bbox(
    bbox: Optional[List[float]],
    page_w: Optional[float],
    page_h: Optional[float],
    service_w: float,
    service_h: float,
) -> Optional[List[float]]:
    """OCR 服务 bbox（1000 归一化空间）→ 源自然坐标系（PDF 点 / 图片像素）。

    x、y 各自归一化（service 宽高均为 1000，与页面宽高解耦），故 x 按 page_w/service_w、
    y 按 page_h/service_h 缩放。无页面尺寸时原样返回（不换算）。
    """
    if not bbox or len(bbox) < 4 or not page_w or not page_h or not service_w or not service_h:
        return bbox
    sx, sy = page_w / service_w, page_h / service_h
    return [bbox[0] * sx, bbox[1] * sy, bbox[2] * sx, bbox[3] * sy]

# 行内公式 \( ... \)（服务实测输出格式）
_INLINE_FORMULA_RE = re.compile(r"\\\((.+?)\\\)", re.S)


def _text_to_runs(text: str, idc: "_Idc") -> List[Any]:
    """把段落文本按 \(..\) 拆成 RunNode / InlineFormulaNode 交替的 runs。"""
    runs: List[Any] = []
    pos = 0
    for m in _INLINE_FORMULA_RE.finditer(text):
        if m.start() > pos:
            runs.append(RunNode(id=idc.run(), text=text[pos:m.start()]))
        runs.append(InlineFormulaNode(id=idc.run(), latex=m.group(1).strip()))
        pos = m.end()
    if pos < len(text):
        runs.append(RunNode(id=idc.run(), text=text[pos:]))
    if not runs:
        runs.append(RunNode(id=idc.run(), text=text))
    return runs


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
    page_w: Optional[float] = None,
    page_h: Optional[float] = None,
    service_w: float = 1000.0,
    service_h: float = 1000.0,
) -> List[BlockNode]:
    """单页 markdown+parsing_res_list → BlockNode 列表（带 provenance）。"""
    elements = parse_markdown(markdown)
    content_blocks = [b for b in (parsing_res_list or []) if b.get("block_label") not in DROP_LABELS]

    out: List[BlockNode] = []
    for i, el in enumerate(elements):
        bbox = content_blocks[i].get("block_bbox") if i < len(content_blocks) else None
        bbox = _convert_bbox(bbox, page_w, page_h, service_w, service_h)
        prov = Provenance(source_type=SourceType.OCR, page_index=page_index, bbox=bbox)
        node = _element_to_node(el, images, page_index, idc, image_out_dir, extract_images, _img_counter, prov)
        if node is not None:
            out.append(node)
    # 过滤空文本块（Phase 1L）
    out = [b for b in out if not (isinstance(b, (HeadingNode, ParagraphNode)) and not (b.text or "").strip())]
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
        text = el["text"]
        return ParagraphNode(
            id=idc.block(), text=text, runs=_text_to_runs(text, idc), provenance=prov
        )

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
