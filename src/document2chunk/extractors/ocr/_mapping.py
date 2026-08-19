"""方案 B 的映射层：parsing_res_list（单一数据源）→ BlockNode 列表。

- 遍历 parsing_res_list，按 block_label 路由：DROP 噪声 / title→HeadingNode /
  text→ParagraphNode（连续编号项分组 ListNode）/ table→TableNode / image,chart→ImageNode。
- block_content 自包含（标题带 ATX 前缀、表格 HTML、图片 <img src> 与 images
  字典 key 直接匹配），不再依赖整页 markdown 的索引对齐——issues5：列表合并
  （N 个 prl block → 1 个 markdown 元素）与页眉/页码 DROP 都会使索引错位，
  页码文本拿到 text 的 bbox 后 DROP 永不触发。
- det_scores 与 parsing_res_list 同源同序，按 prl 自身索引取，天然对齐。
- prl 缺失（服务变体）时退回 markdown 建结构（bbox None，文本不丢）。
"""

from __future__ import annotations

import base64
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from lxml import html as lxml_html

from document2chunk.extractors.ocr._markdown import (
    _HTML_IMG_ALT_RE,
    _HTML_IMG_RE,
    _IMAGE_LINE_RE,
    parse_markdown,
)
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

# 真实服务 label 实测（TB10182-2017 全 44 页）：doc_title/paragraph_title/text/
# number/header/header_image/figure_title/vision_footnote/content/chart/image/table。
DROP_LABELS = {"page_number", "header", "footer", "number", "header_image"}
TITLE_LABELS = {"title", "doc_title", "paragraph_title"}
IMAGE_LABELS = {"image", "chart"}
EQUATION_LABELS = {"equation", "formula"}

_ATX_RE = re.compile(r"^(#{1,6})\s+(.*)$", re.S)
_OL_ITEM_RE = re.compile(r"^(\d+)[).]\s+(.*)$", re.S)
_UL_ITEM_RE = re.compile(r"^[-*]\s+(.*)$", re.S)


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
    r"""把段落文本按 \(..\) 拆成 RunNode / InlineFormulaNode 交替的 runs。"""
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


def _strip_layout_tags(b: str) -> str:
    """剥离 <div>/<span> 布局包装（保留内部文本；<img>/<table> 不在此处理）。"""
    if "<div" in b or "</div>" in b or "<span" in b or "</span>" in b:
        b = re.sub(r"</?div[^>]*>", "", b, flags=re.I)
        b = re.sub(r"</?span[^>]*>", "", b, flags=re.I)
    return b.strip()


def _strip_atx(text: str) -> Tuple[int, str]:
    """'## 前言' → (2, '前言')；无前缀 → (1, 原文)。"""
    m = _ATX_RE.match(text)
    if m:
        return len(m.group(1)), m.group(2).strip()
    return 1, text


def _union_bbox(boxes: List[Optional[List[float]]]) -> Optional[List[float]]:
    valid = [b for b in boxes if b and len(b) >= 4]
    if not valid:
        return None
    return [
        min(b[0] for b in valid),
        min(b[1] for b in valid),
        max(b[2] for b in valid),
        max(b[3] for b in valid),
    ]


def _extract_img_ref(raw: str) -> Tuple[str, str]:
    """从 block_content 提取图片 ref/alt（HTML <img> 或 markdown 行均可）。"""
    m = _HTML_IMG_RE.search(raw)
    if m:
        alt_m = _HTML_IMG_ALT_RE.search(raw)
        alt = (alt_m.group(1).strip() if alt_m and alt_m.group(1).strip() else "Image")
        return m.group(1), alt
    m = _IMAGE_LINE_RE.match(raw.strip())
    if m:
        return m.group(2), m.group(1) or "Image"
    return "", "Image"


def _strip_math_delimiters(raw: str) -> str:
    """剥块公式定界符：\\[..\\] / $$..$$ / 裸 latex。"""
    s = raw.strip()
    if s.startswith("\\[") and s.endswith("\\]"):
        return s[2:-2].strip()
    if s.startswith("$$") and s.endswith("$$") and len(s) >= 4:
        return s[2:-2].strip()
    return s


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
    det_scores: Optional[List[float]] = None,
) -> List[BlockNode]:
    """单页 parsing_res_list → BlockNode 列表（带 provenance + confidence）。

    方案 B（issues5）：以 parsing_res_list 为单一数据源，逐块按 block_label
    路由建节点；DROP 过滤与建节点同一遍历，不存在两列表索引对齐问题。
    markdown 仅在 prl 缺失时作兜底（bbox None）。
    """
    prl = parsing_res_list or []

    # 兜底：无 prl（服务变体）→ markdown 建结构，bbox/confidence 全空
    if not prl:
        out: List[BlockNode] = []
        prov = Provenance(source_type=SourceType.OCR, page_index=page_index)
        for el in parse_markdown(markdown or ""):
            node = _element_to_node(el, images, page_index, idc, image_out_dir, extract_images, _img_counter, prov)
            if node is not None:
                out.append(node)
        return _filter_empty(out)

    nodes: List[BlockNode] = []
    pending: Optional[Dict[str, Any]] = None  # 待提交的连续编号列表

    def flush() -> None:
        nonlocal pending
        if pending:
            nodes.append(_pending_to_list_node(pending, page_index, idc))
            pending = None

    for i, block in enumerate(prl):
        label = (block.get("block_label") or "").strip()
        if label in DROP_LABELS:
            continue
        raw = (block.get("block_content") or "").strip()
        if not raw and label not in IMAGE_LABELS:
            continue  # 空内容块（图片标签块除外——仍出占位）

        bbox = _convert_bbox(block.get("block_bbox"), page_w, page_h, service_w, service_h)
        conf = det_scores[i] if (det_scores and i < len(det_scores)) else None
        prov = Provenance(source_type=SourceType.OCR, page_index=page_index, bbox=bbox, confidence=conf)

        # 连续编号 text 项 → ListNode 分组（保原序号，issues4）
        if label == "text":
            plain = _strip_layout_tags(raw)
            if plain and not plain.lower().startswith("<table") and "<img" not in plain.lower():
                m = _OL_ITEM_RE.match(plain)
                if m:
                    if pending and pending["ordered"]:
                        pending["items"].append((plain, prov))
                    else:
                        flush()
                        pending = {"ordered": True, "items": [(plain, prov)]}
                    continue
                m = _UL_ITEM_RE.match(plain)
                if m:
                    item_text = m.group(1).strip()
                    if pending and not pending["ordered"]:
                        pending["items"].append((item_text, prov))
                    else:
                        flush()
                        pending = {"ordered": False, "items": [(item_text, prov)]}
                    continue

        flush()
        node = _block_to_node(label, raw, images, page_index, idc, image_out_dir, extract_images, _img_counter, prov)
        if node is not None:
            nodes.append(node)

    flush()
    return _filter_empty(nodes)


def _filter_empty(nodes: List[BlockNode]) -> List[BlockNode]:
    """过滤空文本块（Phase 1L）。"""
    return [b for b in nodes if not (isinstance(b, (HeadingNode, ParagraphNode)) and not (b.text or "").strip())]


def _pending_to_list_node(pending: Dict[str, Any], page_index: int, idc: _Idc) -> ListNode:
    items = pending["items"]  # [(text, prov)]
    first_prov = items[0][1]
    prov = Provenance(
        source_type=SourceType.OCR,
        page_index=page_index,
        bbox=_union_bbox([p.bbox for _, p in items]),
        confidence=first_prov.confidence,
    )
    return ListNode(
        id=idc.block(),
        ordered=pending["ordered"],
        items=[
            ListItemNode(
                id=idc.cell_id(),
                level=0,
                blocks=[ParagraphNode(id=idc.block(), text=t, provenance=p)],
            )
            for t, p in items
        ],
        provenance=prov,
    )


def _block_to_node(
    label: str,
    raw: str,
    images: Dict[str, str],
    page_index: int,
    idc: _Idc,
    image_out_dir: Optional[str],
    extract_images: bool,
    _img_counter: List[int],
    prov: Provenance,
) -> Optional[BlockNode]:
    """单个 prl block → BlockNode（按 label + 内容格式路由）。"""
    if label in TITLE_LABELS:
        level, text = _strip_atx(_strip_layout_tags(raw))
        if not text:
            return None
        return HeadingNode(
            id=idc.block(),
            level=min(max(level, 1), 9),
            text=text,
            provenance=prov,
        )

    if label == "table" or raw.lstrip().lower().startswith("<table"):
        return _html_table_to_node(raw, idc, prov)

    if label in IMAGE_LABELS or "<img" in raw.lower():
        ref, alt = _extract_img_ref(raw)
        return _image_to_node({"ref": ref, "alt": alt}, images, page_index, idc, image_out_dir, extract_images, _img_counter, prov)

    if label in EQUATION_LABELS:
        return FormulaNode(id=idc.block(), latex=_strip_math_delimiters(raw), provenance=prov)

    # 默认段落：text / figure_title / vision_footnote / content / 未知 label
    text = _strip_layout_tags(raw)
    if not text:
        return None
    return ParagraphNode(id=idc.block(), text=text, runs=_text_to_runs(text, idc), provenance=prov)


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
    """markdown 元素 → BlockNode（仅 prl 缺失的兜底路径使用）。"""
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
