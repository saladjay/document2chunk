"""OcrExtractor —— 扫描 PDF/图片 → ExtractionResult（编排：按页调服务 + 映射）。"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from document2chunk.extractors.ocr._chunker import iter_pages, page_count
from document2chunk.extractors.ocr._client import OcrServiceClient
from document2chunk.extractors.ocr._config import OcrConfig
from document2chunk.extractors.ocr._exceptions import OcrServiceError
from document2chunk.extractors.ocr._mapping import _Idc, build_page_blocks
from document2chunk.ir import DocumentMetadata, ExtractionResult, SourceType


def _read_bytes(source) -> bytes:
    if isinstance(source, (bytes, bytearray)):
        return bytes(source)
    with open(str(source), "rb") as f:
        return f.read()


class OcrExtractor:
    """扫描件/图片/复杂版式 → IR（远程 PaddleOCR 服务，D11）。"""

    source_type: SourceType = SourceType.OCR

    def __init__(self, config: Optional[OcrConfig] = None, *, client: Optional[OcrServiceClient] = None) -> None:
        self._cfg = config or OcrConfig.from_env()
        self._client = client or OcrServiceClient(self._cfg)

    def extract(
        self,
        source,
        *,
        options=None,
        image_out_dir: Optional[str] = None,
    ) -> ExtractionResult:
        data = _read_bytes(source)
        source_file = Path(source).name if isinstance(source, (str, Path)) else None

        extract_images = bool(getattr(options, "extract_images", True)) if options else True
        model = getattr(options, "ocr_model", None) if options else None
        if not model:
            try:
                model = self._client.active_model()
            except OcrServiceError:
                model = self._cfg.model

        pcount = page_count(data)
        idc = _Idc()
        img_counter = [0]
        blocks = []

        for page_index, media, fname in iter_pages(data, source_file or "source"):
            resp = self._client.parse(media, fname, model=model)
            lp_list = resp.get("layoutParsingResults") or []
            lp = lp_list[0] if lp_list else {}
            # 优先用每页 markdown，回退顶层 markdown
            md = ((lp.get("markdown") or {}).get("text")) or resp.get("markdown", "")
            images = resp.get("images") or (lp.get("markdown") or {}).get("images") or {}
            prl = lp.get("parsing_res_list") or []
            page_blocks = build_page_blocks(
                md, prl, images, page_index, idc, image_out_dir, extract_images, img_counter
            )
            blocks.extend(page_blocks)

        metadata = DocumentMetadata(
            source_type=SourceType.OCR,
            source_file=source_file,
            page_count=pcount,
            generator="paddleocr-service",
        )
        return ExtractionResult(content=blocks, metadata=metadata, toc_entries=None)
