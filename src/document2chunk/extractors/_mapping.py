"""element(dict) → BlockNode 映射层（pdf/ocr 共享）。

把 span 管线产出的 pipeline element dict（schema 见 designs/003 §6）映射为
ir-model 节点（designs/003 §8、specs/pdf-extractor §4）：

- ``title``/``heading`` → :class:`HeadingNode`（level 取 element.level，1–9）
- ``paragraph`` → :class:`ParagraphNode`
- ``table`` → :class:`TableNode`（优先 element.table_rows，否则解析 markdown）
- ``image`` → :class:`ImageNode`（image_id + 尺寸 EMU）
- ``toc_entry``/``toc_title`` → :class:`TocEntry` 信号（不进 content）
- ``page_number`` → 丢弃（不进 content）

span → :class:`RunNode`：bbox 落在 ``RunNode.provenance.bbox``（禁止保留独立 span 类型）。
"""

from __future__ import annotations

import re
from typing import Any, Optional

from document2chunk.ir import (
    BlockNode,
    HeadingNode,
    ImageNode,
    ListItemNode,
    ListNode,
    ParagraphNode,
    Provenance,
    RunNode,
    RunProperties,
    SourceType,
    TableCellNode,
    TableRowNode,
    TableNode,
    TocEntry,
)

# PDF 点 → EMU 换算（914400 EMU/英寸 ÷ 72 pt/英寸 = 12700）
_PT_PER_EMU = 12700

# OCR 低置信阈值（specs/ocr-extractor §3）
_OCR_LOW_CONFIDENCE = 0.5


def _has_font_token(font: str | None, *tokens: str) -> bool:
    """字体名是否包含任一特征词（Bold/Italic/Oblique，大小写不敏感）。"""
    if not font:
        return False
    lower = font.lower()
    return any(tok.lower() in lower for tok in tokens)


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


def _bbox_round(bbox: Any) -> Optional[list[float]]:
    if not bbox or len(bbox) < 4:
        return None
    return [round(float(v), 2) for v in bbox[:4]]


def _make_provenance(
    *,
    source_type: SourceType,
    page_index: Optional[int],
    bbox: Any,
    confidence: Optional[float] = None,
) -> Optional[Provenance]:
    """构造块级 provenance（PDF/OCR 携带）。"""
    return Provenance(
        source_type=source_type,
        page_index=page_index,
        bbox=_bbox_round(bbox),
        confidence=confidence,
    )


def span_to_run(
    span: dict,
    *,
    source_type: SourceType,
    page_index: Optional[int],
    idgen: _IdGen,
) -> RunNode:
    """span dict → RunNode（provenance.bbox = span.bbox）。"""
    font = span.get("font", "")
    flags = span.get("flags", 0) or 0
    size = span.get("size")

    return RunNode(
        id=idgen.run(),
        text=span.get("text", ""),
        style=RunProperties(
            font=font or None,
            font_size=round(float(size), 2) if size else None,
            bold=bool(flags & 0x10) or _has_font_token(font, "Bold"),
            italic=bool(flags & 0x02) or _has_font_token(font, "Italic", "Oblique"),
        ),
        provenance=Provenance(
            source_type=source_type,
            page_index=page_index,
            bbox=_bbox_round(span.get("bbox")),
            confidence=span.get("confidence"),
        ),
    )


def _spans_to_runs(
    spans: list[dict] | None,
    *,
    source_type: SourceType,
    page_index: Optional[int],
    idgen: _IdGen,
) -> list[RunNode]:
    if not spans:
        return []
    return [
        span_to_run(s, source_type=source_type, page_index=page_index, idgen=idgen)
        for s in spans
    ]


# ==================== 表格 ====================


def _parse_markdown_table(markdown: str) -> list[list[str]]:
    """从 markdown 表格文本解析回二维网格（无 table_rows 时的兜底）。"""
    if not markdown:
        return []
    rows: list[list[str]] = []
    for line in markdown.strip().splitlines():
        line = line.strip()
        if not line or not line.startswith("|") or not line.endswith("|"):
            continue
        # 跳过分隔行 | --- | --- |
        cells = [c.strip() for c in line.strip("|").split("|")]
        if cells and all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue
        rows.append(cells)
    return rows


def _table_to_table_node(
    elem: dict,
    *,
    source_type: SourceType,
    page_index: Optional[int],
    idgen: _IdGen,
) -> TableNode:
    table_rows = elem.get("table_rows")
    if not table_rows:
        table_rows = _parse_markdown_table(elem.get("markdown", ""))

    rows = []
    for r, row in enumerate(table_rows or []):
        cells = []
        for cell in row:
            text = (str(cell) if cell is not None else "").strip()
            cells.append(
                TableCellNode(
                    id=idgen.cell(),
                    blocks=[
                        ParagraphNode(
                            id=idgen.block(),
                            text=text,
                        )
                    ],
                )
            )
        rows.append(
            TableRowNode(id=idgen.row(), is_header=(r == 0), cells=cells)
        )

    return TableNode(
        id=idgen.block(),
        rows=rows,
        provenance=_make_provenance(
            source_type=source_type,
            page_index=page_index,
            bbox=elem.get("bbox"),
        ),
    )


# ==================== 图片 ====================


def _image_to_image_node(
    elem: dict,
    *,
    source_type: SourceType,
    page_index: Optional[int],
    image_info: dict | None,
    idgen: _IdGen,
) -> ImageNode:
    image_id = elem.get("text", "") or elem.get("image_id", "")
    fmt = None
    alt = None
    if image_info:
        fmt = image_info.get("format")
        alt = image_info.get("alt")
    # 补扩展名：确保 image_id 含 .png/.jpg 等（与落盘文件名一致，markdown 引用对得上）
    if fmt and image_id and "." not in image_id.rsplit("/", 1)[-1]:
        image_id = f"{image_id}.{fmt}"

    # 尺寸来源：优先 image_info（PDF 提取前端）；否则用 element bbox 估算（OCR 占位）
    width_pt = height_pt = None
    fmt = None
    alt = None
    if image_info:
        width_pt = image_info.get("width")
        height_pt = image_info.get("height")
        fmt = image_info.get("format")
        alt = image_info.get("alt")
    else:
        bbox = elem.get("bbox") or []
        if len(bbox) >= 4:
            width_pt = bbox[2] - bbox[0]
            height_pt = bbox[3] - bbox[1]

    width_emu = round(float(width_pt) * _PT_PER_EMU) if width_pt else None
    height_emu = round(float(height_pt) * _PT_PER_EMU) if height_pt else None

    return ImageNode(
        id=idgen.block(),
        image_id=image_id,
        format=fmt,
        width_emu=width_emu,
        height_emu=height_emu,
        alt=alt,
        provenance=_make_provenance(
            source_type=source_type,
            page_index=page_index,
            bbox=elem.get("bbox"),
        ),
    )


# ==================== 元素 → 块 ====================


def _element_page_index(elem: dict) -> Optional[int]:
    pi = elem.get("page_index")
    return pi if isinstance(pi, int) else None


def _toc_page_from_text(text: str) -> Optional[int]:
    """从目录条目文本尾部提取页码（best-effort，无则 None）。"""
    m = re.search(r"(\d+)\s*$", text.strip())
    return int(m.group(1)) if m else None


def elements_to_blocks(
    pages_elements: list[list[dict]],
    *,
    source_type: SourceType,
    image_infos_by_page: dict[int, list[dict]] | None = None,
) -> tuple[list[BlockNode], list[TocEntry]]:
    """把多页 pipeline elements 映射为 (content 块序列, toc_entries 信号)。

    - content 按 (page_index, y_top, x0) 稳定排序（pdf-extractor spec §3）。
    - toc_entry/toc_title 收集为 TocEntry 信号，不进 content。
    - page_number 丢弃。
    """
    idgen = _IdGen()
    image_infos_by_page = image_infos_by_page or {}

    # 收集 (sort_key, elem)
    flat: list[tuple[tuple, dict]] = []
    for elems in pages_elements:
        for elem in elems:
            bbox = elem.get("bbox") or [0, 0, 0, 0]
            page_idx = _element_page_index(elem) or 0
            y_top = bbox[1] if len(bbox) > 1 else 0
            x0 = bbox[0] if len(bbox) > 0 else 0
            flat.append(((page_idx, y_top, x0), elem))

    # 稳定排序（保留同位置元素的输入顺序）
    flat.sort(key=lambda x: x[0])

    blocks: list[BlockNode] = []
    toc_entries: list[TocEntry] = []

    for _, elem in flat:
        elem_type = elem.get("type")
        page_index = _element_page_index(elem)
        confidence = elem.get("confidence")
        low_conf = bool(elem.get("low_confidence")) or (
            source_type == SourceType.OCR
            and confidence is not None
            and confidence < _OCR_LOW_CONFIDENCE
        )

        if elem_type in ("title", "heading"):
            level = elem.get("level")
            if not isinstance(level, int) or not (1 <= level <= 9):
                level = 1  # 兜底（OCR 未赋级时）
            metadata: dict[str, Any] = {}
            if "heading_confidence" in elem:
                metadata["heading_confidence"] = elem["heading_confidence"]
            if elem.get("heading_level_conf_history"):
                metadata["heading_level_conf_history"] = elem[
                    "heading_level_conf_history"
                ]
            if low_conf:
                metadata["low_confidence"] = True
            blocks.append(
                HeadingNode(
                    id=idgen.block(),
                    level=level,
                    text=elem.get("text", ""),
                    runs=_spans_to_runs(
                        elem.get("spans"),
                        source_type=source_type,
                        page_index=page_index,
                        idgen=idgen,
                    ),
                    provenance=_make_provenance(
                        source_type=source_type,
                        page_index=page_index,
                        bbox=elem.get("bbox"),
                        confidence=confidence,
                    ),
                    metadata=metadata,
                )
            )

        elif elem_type == "paragraph":
            md: dict[str, Any] = {}
            if low_conf:
                md["low_confidence"] = True
            blocks.append(
                ParagraphNode(
                    id=idgen.block(),
                    text=elem.get("text", ""),
                    runs=_spans_to_runs(
                        elem.get("spans"),
                        source_type=source_type,
                        page_index=page_index,
                        idgen=idgen,
                    ),
                    provenance=_make_provenance(
                        source_type=source_type,
                        page_index=page_index,
                        bbox=elem.get("bbox"),
                        confidence=confidence,
                    ),
                    metadata=md,
                )
            )

        elif elem_type == "table":
            blocks.append(
                _table_to_table_node(
                    elem,
                    source_type=source_type,
                    page_index=page_index,
                    idgen=idgen,
                )
            )

        elif elem_type == "image":
            # 查找该页同名 image_info 以取尺寸
            image_info = None
            for info in image_infos_by_page.get(page_index or 0, []):
                if info.get("image_id") == elem.get("text"):
                    image_info = info
                    break
            blocks.append(
                _image_to_image_node(
                    elem,
                    source_type=source_type,
                    page_index=page_index,
                    image_info=image_info,
                    idgen=idgen,
                )
            )

        elif elem_type == "list":
            # PDF 列表识别有限；保留为单元素列表项
            blocks.append(
                ListNode(
                    id=idgen.block(),
                    items=[
                        ListItemNode(
                            id=idgen.block(),
                            level=int(elem.get("level") or 0),
                            blocks=[
                                ParagraphNode(
                                    id=idgen.block(), text=elem.get("text", "")
                                )
                            ],
                        )
                    ],
                    provenance=_make_provenance(
                        source_type=source_type,
                        page_index=page_index,
                        bbox=elem.get("bbox"),
                    ),
                )
            )

        elif elem_type in ("toc_entry", "toc_title"):
            # TOC 信号：校准层级 / 可选 TocNode 导出；不进 content
            toc_entries.append(
                TocEntry(
                    text=elem.get("text", ""),
                    level=elem.get("level") if isinstance(elem.get("level"), int) else None,
                    page=_toc_page_from_text(elem.get("text", "")),
                )
            )

        elif elem_type == "page_number":
            # 丢弃
            continue

        else:
            # 未分类（理论上管线已分类）→ 兜底为段落
            blocks.append(
                ParagraphNode(
                    id=idgen.block(),
                    text=elem.get("text", ""),
                    runs=_spans_to_runs(
                        elem.get("spans"),
                        source_type=source_type,
                        page_index=page_index,
                        idgen=idgen,
                    ),
                    provenance=_make_provenance(
                        source_type=source_type,
                        page_index=page_index,
                        bbox=elem.get("bbox"),
                    ),
                )
            )

    return blocks, toc_entries
