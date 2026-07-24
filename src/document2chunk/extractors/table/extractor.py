"""TableExtractor：任意 PDF/图片 → 高质量 TableNode（designs/008 §6）。

调用远程表格识别服务（``/api/table-recognition``），把每张表解析为 :class:`TableNode`
（保留 colspan/rowspan + 文字 + 单元格框 provenance）。输出 :class:`ExtractionResult`。

三种重建模式（``table_reconstruct`` option）：
- ``auto``（默认）：``cell_box_list`` 几何重建（拓扑正确），失败回退 html。仅依赖 httpx。
- ``geo``：强求几何（仍带静默回退）。
- ``html``：直走 html（逃生口）。
- ``geo_ocr``：几何网格 + **每格 box-bearing OCR 文字**（根治合并表头文字错位）。
  额外依赖 pymupdf（PDF 渲染）+ paddleocr（OCR）；PDF 源会渲染页 → 图片调服务取校准框
  → 本地 OCR 按格匹配。可 ``page_image_dir`` 落盘页图便于对比。
"""

from __future__ import annotations

import os
from io import BytesIO
from pathlib import Path
from typing import Any, Optional, Union

from document2chunk.extractors.table._client import TableServiceClient
from document2chunk.extractors.table._config import TableConfig
from document2chunk.extractors.table._geo_reconstruct import (
    try_geo_ocr_to_table_node,
    try_geo_to_table_node,
)
from document2chunk.extractors.table._html_parser import _Idc, html_to_table_node
from document2chunk.ir import (
    BlockNode,
    DocumentMetadata,
    ExtractionResult,
    SourceType,
)

SourceLike = Union[str, Path, bytes]


class TableExtractor:
    """任意 PDF/图片 → ExtractionResult(TableNode 列表)。"""

    def __init__(
        self,
        *,
        client: Optional[TableServiceClient] = None,
        config: Optional[TableConfig] = None,
    ) -> None:
        self._client = client
        self._config = config

    @property
    def client(self) -> TableServiceClient:
        if self._client is None:
            self._client = TableServiceClient(self._config)
        return self._client

    def extract(self, source: SourceLike, *, options: Any = None) -> ExtractionResult:
        opts = _normalize_options(options)
        data, filename = _read_source(source)
        source_type = SourceType.PDF if data[:5] == b"%PDF-" else SourceType.OCR

        fmt = opts.get("table_fmt")
        page_range = opts.get("page_range", "all")
        result = self.client.recognize(data, filename, fmt=fmt, page_range=page_range)

        tables = result.get("tables") or []
        idc = _Idc()
        mode = opts.get("table_reconstruct", "auto")  # auto | geo | html | geo_ocr
        header_rows = opts.get("header_rows")

        if mode == "geo_ocr":
            blocks, pages = self._extract_geo_ocr(data, source_type, tables, opts, idc)
        else:
            blocks, pages = self._extract_geo_or_html(
                tables, mode, source_type, idc, header_rows
            )

        metadata = DocumentMetadata(
            source_type=source_type,
            source_file=filename,
            page_count=(max(pages) + 1) if pages else 0,
            generator="paddleocr-table",
            custom={
                "table_count": len(blocks),
                "formats": result.get("formats", []),
                "table_reconstruct": mode,
            },
        )
        return ExtractionResult(content=blocks, metadata=metadata)

    # ------------------------------------------------------------------
    # auto / geo / html：仅依赖 httpx
    # ------------------------------------------------------------------

    def _extract_geo_or_html(self, tables, mode, source_type, idc, header_rows):
        blocks: list[BlockNode] = []
        for t in tables:
            page = _page(t)
            html = t.get("html", "")
            j = t.get("json") or {}
            cell_boxes = j.get("cell_box_list")

            node: Optional[BlockNode] = None
            # 几何重建优先：用 cell_box_list 还原真实网格拓扑（复杂合并表头 html 不可信）。
            if mode in ("auto", "geo") and cell_boxes:
                node = try_geo_to_table_node(
                    cell_boxes,
                    html=html,
                    page_index=page,
                    source_type=source_type,
                    idc=idc,
                    rec_texts=j.get("rec_texts"),
                    rec_scores=j.get("rec_scores"),
                    header_rows=header_rows,
                )
            if node is None:  # geo 失败 或 mode=="html"
                node = html_to_table_node(
                    html,
                    page_index=page,
                    source_type=source_type,
                    cell_boxes=cell_boxes,
                    idc=idc,
                )
            blocks.append(node)
        pages = sorted({_page(t) for t in tables})
        return blocks, pages

    # ------------------------------------------------------------------
    # geo_ocr：几何网格 + 每格 box-bearing OCR 文字
    # ------------------------------------------------------------------

    def _extract_geo_ocr(self, data, source_type, tables, opts, idc):
        from document2chunk.extractors.table._render import (
            image_to_png_bytes,
            render_pdf_page,
        )

        fmt = opts.get("table_fmt")
        ocr = opts.get("ocr")  # 注入引擎（测试）；None → lazy 创建
        page_image_dir = opts.get("page_image_dir")
        dpi = opts.get("dpi", 200)
        header_rows = opts.get("header_rows")
        blocks: list[BlockNode] = []
        pages_set: set[int] = set()

        if source_type == SourceType.PDF:
            # PDF call 已给出每表所在页；按页分组
            by_page: dict[int, list[dict]] = {}
            for t in tables:
                by_page.setdefault(_page(t), []).append(t)
            for page in sorted(by_page):
                try:
                    img = render_pdf_page(data, page, dpi=dpi)
                except Exception:
                    img = None
                if img is not None:
                    if page_image_dir:
                        _save_img(img, page_image_dir, page)
                    # 图片调服务 → 校准到本图空间的 cell_box_list
                    img_tables = self._image_recognize(img, page, fmt)
                    if img_tables:
                        for it in img_tables:
                            blocks.append(
                                self._build_geo_ocr_node(
                                    it, img, page, source_type, idc, ocr, header_rows
                                )
                            )
                            pages_set.add(page)
                        continue
                # 回退：PDF call 该页表走 html
                for t in by_page[page]:
                    blocks.append(
                        html_to_table_node(
                            t.get("html", ""),
                            page_index=page,
                            source_type=source_type,
                            cell_boxes=(t.get("json") or {}).get("cell_box_list"),
                            idc=idc,
                        )
                    )
                    pages_set.add(page)
        else:
            # 图片源：源图即页图；首调 boxes 已校准到源图空间
            img = _load_image(data)
            if img is not None and page_image_dir:
                _save_img(img, page_image_dir, 0)
            for t in tables:
                page = _page(t)
                j = t.get("json") or {}
                cb = j.get("cell_box_list")
                blocks.append(
                    self._build_geo_ocr_node(
                        {"html": t.get("html", ""), "json": j},
                        img,
                        page,
                        source_type,
                        idc,
                        ocr,
                        header_rows,
                        cell_boxes_override=cb,
                    )
                )
                pages_set.add(page)
        return blocks, sorted(pages_set)

    def _image_recognize(self, img, page, fmt):
        """把渲染页图送服务 → 该页校准 tables。"""
        from document2chunk.extractors.table._render import image_to_png_bytes

        try:
            res = self.client.recognize(
                image_to_png_bytes(img), f"p{page}.png", fmt=fmt, page_range="all"
            )
            return res.get("tables") or []
        except Exception:
            return []

    def _build_geo_ocr_node(
        self, table, img, page, source_type, idc, ocr, header_rows, *, cell_boxes_override=None
    ):
        """geo_ocr → 失败回退 geo → 失败回退 html。"""
        j = table.get("json") or {}
        cb = cell_boxes_override if cell_boxes_override is not None else j.get("cell_box_list")
        html = table.get("html", "")
        if img is not None and cb:
            node = try_geo_ocr_to_table_node(
                cb,
                img,
                ocr=ocr,
                page_index=page,
                source_type=source_type,
                idc=idc,
                header_rows=header_rows,
            )
            if node is not None:
                return node
        if cb:  # geo_ocr 失败 → 几何（用校准框 + html 文字）
            node = try_geo_to_table_node(
                cb,
                html=html,
                page_index=page,
                source_type=source_type,
                idc=idc,
                header_rows=header_rows,
            )
            if node is not None:
                return node
        return html_to_table_node(
            html,
            page_index=page,
            source_type=source_type,
            cell_boxes=cb,
            idc=idc,
        )


def _page(t: dict) -> int:
    """表所在页（0-based）；服务对图片源可能返回 ``page: null`` → 0。"""
    p = t.get("page")
    return p if isinstance(p, int) else 0


def _read_source(source: SourceLike) -> tuple[bytes, str]:
    if isinstance(source, (bytes, bytearray)):
        return bytes(source), "table-input"
    path = Path(source)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {path}")
    return path.read_bytes(), path.name


def _load_image(data: bytes):
    """bytes → PIL.Image（RGB）；失败返回 None。"""
    try:
        from PIL import Image

        return Image.open(BytesIO(data)).convert("RGB")
    except Exception:
        return None


def _save_img(img, directory: str, page: int) -> None:
    """落盘页图（对比用）；失败静默。"""
    try:
        os.makedirs(directory, exist_ok=True)
        img.save(os.path.join(directory, f"page_{page}.png"))
    except Exception:
        pass


def _normalize_options(options: Any) -> dict:
    if options is None:
        return {}
    if isinstance(options, dict):
        return options
    out: dict = {}
    for k in (
        "table_fmt",
        "page_range",
        "table_reconstruct",
        "header_rows",
        "ocr",
        "page_image_dir",
        "dpi",
    ):
        v = getattr(options, k, None)
        if v is not None:
            out[k] = v
    return out
