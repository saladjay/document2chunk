"""OcrExtractor —— 扫描 PDF/图片 → ExtractionResult（编排：按页调服务 + 映射）。"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from document2chunk.extractors.ocr._chunker import iter_pages, page_count
from document2chunk.extractors.ocr._client import OcrServiceClient
from document2chunk.extractors.ocr._config import OcrConfig
from document2chunk.extractors.ocr._exceptions import OcrServiceError
from document2chunk.heading import calibrate, filter_cross_page_noise, join_cross_page_paragraphs
from document2chunk.extractors.ocr._mapping import _Idc, build_page_blocks
from document2chunk.ir import DocumentMetadata, ExtractionResult, SourceType


def _read_bytes(source) -> bytes:
    if isinstance(source, (bytes, bytearray)):
        return bytes(source)
    with open(str(source), "rb") as f:
        return f.read()


def _dump_response(dump_dir, page_index: int, resp: dict) -> None:
    """落盘每页原始服务响应（中间结果）。images 的 base64 脱敏为长度标记（避免巨文件）。"""
    import json
    import os

    os.makedirs(str(dump_dir), exist_ok=True)

    def _mask(imgs):
        if not isinstance(imgs, dict):
            return imgs
        return {
            k: (f"<base64 {len(v)} chars>" if isinstance(v, str) else v) for k, v in imgs.items()
        }

    sanitized = dict(resp)
    sanitized["images"] = _mask(sanitized.get("images") or {})
    for lp in sanitized.get("layoutParsingResults") or []:
        m = lp.get("markdown") if isinstance(lp, dict) else None
        if isinstance(m, dict) and m.get("images"):
            m["images"] = _mask(m["images"])
    path = os.path.join(str(dump_dir), f"page_{page_index:03d}_response.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sanitized, f, ensure_ascii=False, indent=2)


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
        dump_dir: Optional[str] = None,
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

        for page_index, media, fname, pw, ph in iter_pages(data, source_file or "source"):
            resp = self._client.parse(media, fname, model=model)
            if dump_dir:
                _dump_response(dump_dir, page_index, resp)
            lp_list = resp.get("layoutParsingResults") or []
            lp = lp_list[0] if lp_list else {}
            # 优先用每页 markdown，回退顶层 markdown
            md = ((lp.get("markdown") or {}).get("text")) or resp.get("markdown", "")
            images = resp.get("images") or (lp.get("markdown") or {}).get("images") or {}
            # 真实服务把 parsing_res_list / width / height 放在 prunedResult 下
            # （合成 fixture 放在 lp 顶层——两者都兼容）
            pr = lp.get("prunedResult") or {}
            prl = lp.get("parsing_res_list") or pr.get("parsing_res_list") or []
            # 服务坐标空间（block_bbox 所在），用于把 bbox 换算到源自然坐标系
            sw = float(pr.get("width") or lp.get("width") or 1000)
            sh = float(pr.get("height") or lp.get("height") or 1000)
            page_blocks = build_page_blocks(
                md, prl, images, page_index, idc, image_out_dir, extract_images, img_counter,
                pw, ph, sw, sh,
            )
            blocks.extend(page_blocks)

        metadata = DocumentMetadata(
            source_type=SourceType.OCR,
            source_file=source_file,
            page_count=pcount,
            generator="paddleocr-service",
        )
        # 文档级标题重定级（编号优先 + 高度聚类；大标题抽 metadata）—— designs/004
        blocks = calibrate(blocks, metadata)
        blocks = join_cross_page_paragraphs(blocks)
        blocks = filter_cross_page_noise(blocks)
        return ExtractionResult(content=blocks, metadata=metadata, toc_entries=None)
