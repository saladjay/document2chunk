"""ocr-extractor: 扫描件 / 图片 / 复杂版式 / 长文档 → ExtractionResult（D11 远程服务版）。

实现依据 ``openspec/specs/ocr-extractor/spec.md``（D11 重写）：
- 后端 = **远程 PaddleOCR 服务**（PP-OCRv6 / PaddleOCR-VL / Unlimited-OCR），
  弃本地 paddleocr，不走 span 管线。
- 流程：选模型 → :class:`OcrServiceClient` 调 HTTP API → 服务返回结构化
  ``markdown`` → 共享 :func:`document2chunk.parsers.markdown.markdown_to_blocks`
  建 IR → :class:`ExtractionResult`（``source_type=ocr``）。

OCR 归入「结构化源」家族（与 docx 一致），provenance 默认 None（服务 box 结构
确认后再丰富，见 spec §6）。
"""

from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Any, Optional, Union

from document2chunk.exceptions import InvalidSourceError, OcrServiceError
from document2chunk.extractors._ocr_service import (
    VALID_MODELS,
    OcrConfig,
    OcrServiceClient,
)
from document2chunk.ir import (
    BlockNode,
    DocumentMetadata,
    ExtractionResult,
    SourceType,
)
from document2chunk.parsers.markdown import markdown_to_blocks

SourceLike = Union[str, Path, bytes]

# 长文档页数阈值：超过 → unlimited-ocr
_LONG_PAGE_THRESHOLD = 20
_DEFAULT_MODEL = "paddleocr-vl-1.6"


class OcrExtractor:
    """扫描件 / 图片 / 复杂版式 → ExtractionResult（远程 PaddleOCR 服务）。"""

    source_type: SourceType = SourceType.OCR

    def __init__(
        self,
        *,
        client: Optional[OcrServiceClient] = None,
        config: Optional[OcrConfig] = None,
        default_model: Optional[str] = None,
        long_page_threshold: int = _LONG_PAGE_THRESHOLD,
    ):
        """
        Args:
            client: 注入 :class:`OcrServiceClient`（便于测试用 stub）；None 则按 config/env 新建。
            config: 服务配置（token/endpoint 等）；None 则 :meth:`OcrConfig.from_env`。
            default_model: 默认模型（未显式指定且非长文档时）；None → VL。
            long_page_threshold: 长文档页数阈值（> → unlimited-ocr）。
        """
        self._client = client
        self._config = config
        self._default_model = default_model or _DEFAULT_MODEL
        self._long_page_threshold = long_page_threshold

    @property
    def client(self) -> OcrServiceClient:
        if self._client is None:
            self._client = OcrServiceClient(self._config)
        return self._client

    def extract(
        self,
        source: SourceLike,
        *,
        options: Any = None,
    ) -> ExtractionResult:
        """解析扫描件/图片 → ExtractionResult。"""
        opts = _normalize_options(options)
        data, filename = _read_source(source, opts)

        # 选模型：显式 > 启发式（长文档→unlimited，否则默认 VL）
        model = opts.get("ocr_model") or self._select_model(data, filename)
        if model not in VALID_MODELS:
            raise OcrServiceError(f"未知 OCR 模型：{model}")

        # 确保模型就绪 + 解析
        self.client.ensure_model(model)
        result = self.client.parse(data, filename, model=model)

        # markdown → IR
        markdown = (result.get("markdown") or "").strip()
        blocks = markdown_to_blocks(markdown, source_type=SourceType.OCR)

        # 图片二进制：v1 仅留 image_id（markdown 里的 ref），不附 data
        # （服务 images 结构确认后再按 extract_images 附 bytes，见 spec §5/§7）。
        # 低置信区域（若服务给 confidence）→ metadata.low_confidence（结构确认后补）

        metadata = DocumentMetadata(
            source_type=SourceType.OCR,
            source_file=filename,
            page_count=_page_count(result, data),
            custom={
                "ocr_model": model,
                **({"body_font": "OCR"} if blocks else {}),
            },
        )

        return ExtractionResult(content=blocks, metadata=metadata, toc_entries=None)

    # ---------- 模型选择 ----------

    def _select_model(self, data: bytes, filename: str) -> str:
        """启发式：长 PDF（>阈值页）→ unlimited-ocr；否则默认（VL）。"""
        if _looks_like_pdf(data, filename):
            pages = _pdf_page_count(data)
            if pages and pages > self._long_page_threshold:
                return "unlimited-ocr"
        return self._default_model


# ============================================================
# 源读取与页数
# ============================================================


def _read_source(source: SourceLike, opts: dict[str, Any]) -> tuple[bytes, str]:
    if isinstance(source, (bytes, bytearray)):
        data = bytes(source)
        filename = opts.get("source_file") or "ocr-input"
        return data, filename
    path = Path(source)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {path}")
    return path.read_bytes(), path.name


def _normalize_options(options: Any) -> dict[str, Any]:
    if options is None:
        return {}
    if isinstance(options, dict):
        return options
    result: dict[str, Any] = {}
    for key in ("ocr_model", "source_file", "extract_images", "default_model"):
        val = getattr(options, key, None)
        if val is not None:
            result[key] = val
    return result


def _looks_like_pdf(data: bytes, filename: str) -> bool:
    if data[:5] == b"%PDF-":
        return True
    return filename.lower().endswith(".pdf")


def _pdf_page_count(data: bytes) -> Optional[int]:
    """PDF 页数（lazy pymupdf；不可用则 None）。"""
    try:
        import pymupdf
    except ImportError:
        return None
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
        try:
            return len(doc)
        finally:
            doc.close()
    except Exception:
        return None


def _page_count(result: dict, data: bytes) -> Optional[int]:
    """优先从服务结果取页数；否则按输入估算。"""
    # layoutParsingResults 通常是按页列表
    lpr = result.get("layoutParsingResults")
    if isinstance(lpr, list) and lpr:
        return len(lpr)
    pages = result.get("pages")
    if isinstance(pages, list) and pages:
        return len(pages)
    if _looks_like_pdf(data, ""):
        n = _pdf_page_count(data)
        if n:
            return n
    return None


__all__ = [
    "OcrExtractor",
    "OcrServiceClient",
    "OcrConfig",
]
