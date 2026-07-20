"""TableExtractor：任意 PDF/图片 → 高质量 TableNode（designs/008 §6）。

调用远程表格识别服务（``/api/table-recognition``），把每张表的 HTML 解析为
:class:`TableNode`（保留 colspan/rowspan + 文字 + 单元格框 provenance）。
输出 :class:`ExtractionResult`（content 仅含 TableNode；无正文）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Union

from document2chunk.extractors.table._client import TableServiceClient
from document2chunk.extractors.table._config import TableConfig
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
        blocks: list[BlockNode] = []
        for t in tables:
            page = t.get("page", 0)
            html = t.get("html", "")
            j = t.get("json") or {}
            cell_boxes = j.get("cell_box_list")
            blocks.append(
                html_to_table_node(
                    html,
                    page_index=page,
                    source_type=source_type,
                    cell_boxes=cell_boxes,
                    idc=idc,
                )
            )

        pages = sorted({t.get("page", 0) for t in tables})
        metadata = DocumentMetadata(
            source_type=source_type,
            source_file=filename,
            page_count=(max(pages) + 1) if pages else 0,
            generator="paddleocr-table",
            custom={"table_count": len(tables), "formats": result.get("formats", [])},
        )
        return ExtractionResult(content=blocks, metadata=metadata)


def _read_source(source: SourceLike) -> tuple[bytes, str]:
    if isinstance(source, (bytes, bytearray)):
        return bytes(source), "table-input"
    path = Path(source)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {path}")
    return path.read_bytes(), path.name


def _normalize_options(options: Any) -> dict:
    if options is None:
        return {}
    if isinstance(options, dict):
        return options
    out: dict = {}
    for k in ("table_fmt", "page_range"):
        v = getattr(options, k, None)
        if v is not None:
            out[k] = v
    return out
