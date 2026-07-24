"""OcrExtractor —— 扫描 PDF/图片 → ExtractionResult（编排：按页调服务 + 映射）。"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from document2chunk.extractors.ocr._chunker import iter_pages, page_count
from document2chunk.extractors.ocr._client import OcrServiceClient
from document2chunk.extractors.ocr._config import OcrConfig
from document2chunk.extractors.ocr._exceptions import OcrServiceError
from document2chunk.postprocess import postprocess
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
        page_geometry: dict = {}

        for page_index, media, fname, pw, ph in iter_pages(data, source_file or "source"):
            page_geometry[page_index] = (pw, ph)
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
            # 检测置信度（VL 模型的 layout_det_res.boxes[].score，与 parsing_res_list 1:1 对齐）
            det_boxes = (pr.get("layout_det_res") or {}).get("boxes") or []
            det_scores = [b.get("score") for b in det_boxes] if det_boxes else None
            page_blocks = build_page_blocks(
                md, prl, images, page_index, idc, image_out_dir, extract_images, img_counter,
                pw, ph, sw, sh, det_scores=det_scores,
            )
            # 低置信度过滤
            if self._cfg.min_confidence > 0:
                page_blocks = [
                    b for b in page_blocks
                    if not (b.provenance and b.provenance.confidence is not None
                            and b.provenance.confidence < self._cfg.min_confidence)
                ]
            blocks.extend(page_blocks)

        metadata = DocumentMetadata(
            source_type=SourceType.OCR,
            source_file=source_file,
            page_count=pcount,
            generator="paddleocr-service",
        )
        # 全文档后处理（两路共用：噪声过滤 + 跨页合并 + 标题定级 + 附件拆分，designs/009）
        pp_log: list = []
        main_content, attach_segments = postprocess(
            blocks, metadata,
            toc_entries=None,
            page_geometry=page_geometry,
            use_height_fallback=True,  # OCR：高度比（DOC_TITLE_RATIO）
            _log=pp_log,
        )
        # 中间过程日志
        if dump_dir:
            import json as _json, os as _os
            _os.makedirs(str(dump_dir), exist_ok=True)
            with open(_os.path.join(str(dump_dir), "postprocess_log.json"), "w", encoding="utf-8") as f:
                _json.dump(pp_log, f, ensure_ascii=False, indent=2)
        # 表格 → 高清截图（designs/009）：有 image_out_dir 且 table_image 时挂 table_image_id
        _attach_table_images_ocr(options, image_out_dir, data, main_content, attach_segments)
        result = ExtractionResult(content=main_content, metadata=metadata, toc_entries=None)
        for seg in attach_segments:
            result.attachments.append(ExtractionResult(content=seg, metadata=DocumentMetadata(
                source_type=SourceType.OCR, source_file=source_file, generator="attachment")))
        return result


def _attach_table_images_ocr(options, image_out_dir, data, main_content, attach_segments):
    """OCR 路径复杂表截图（designs/009 §image 模式）：仅 ``table_complex_format="image"`` 且
    有 image_out_dir 时挂 table_image_id。默认 html 模式不截图（复杂表 → HTML 表格）。"""
    if not image_out_dir:
        return
    if options is None:
        opts = {}
    elif isinstance(options, dict):
        opts = options
    else:
        opts = {k: getattr(options, k, None) for k in ("table_image_dpi", "deskew", "table_complex_format")}
    if opts.get("table_complex_format", "html") != "image":
        return
    from document2chunk.extractors._table_image import attach_table_images

    kw = dict(
        image_dir=image_out_dir,
        dpi=float(opts.get("table_image_dpi", 300)),
        deskew=bool(opts.get("deskew", True)),
        mode="merged",
    )
    try:
        attach_table_images(main_content, data, **kw)
        for seg in attach_segments:
            attach_table_images(seg, data, **kw)
    except Exception:
        pass
