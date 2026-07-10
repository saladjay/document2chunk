"""ocr-extractor: 扫描件 PDF / 图片 → ExtractionResult。

实现依据 specs/ocr-extractor/spec.md：
- PaddleOCR 识别文本行（bbox + text + confidence）+ 版面分析（PP-DocLayout：
  text/title/figure/table/footer/number 区域标签）。
- source 感知降级：版面 ``title`` 标签作主信号 → HeadingNode（在 Classification 的
  OCR 分支处理）；字号估算（bbox 高 × 72/DPI）+ 正文基准众数；bold 判断失效
  （OCR flags 恒 0 → AutoLevel bold 规则自然不触发）。
- 复用 :class:`document2chunk.pipeline.SplitPipeline`（source_type="ocr" 分支）。
- confidence < 阈值的行保留但标 ``metadata.low_confidence``；footer/number 区域不进 content。

PaddleOCR 为可选依赖（pyproject ``ocr`` extra）。前端可注入以便单元测试。
"""

from __future__ import annotations

import io
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Protocol, Sequence, Union

from document2chunk.errors import InvalidSourceError
from document2chunk.extractors._mapping import elements_to_blocks
from document2chunk.ir import (
    DocumentMetadata,
    ExtractionResult,
    SourceType,
)
from document2chunk.pipeline import (
    PipelineContext,
    SplitPipeline,
    SplitStages,
    default_split_stages,
)
from document2chunk.pipeline.config import OCR_DPI

_logger = logging.getLogger("document2chunk.extractors.ocr")

SourceLike = Union[str, Path, bytes]

# 默认低置信阈值（specs/ocr-extractor §3）
_DEFAULT_MIN_CONFIDENCE = 0.5

# 不进入 content 的版面标签（页眉/页脚/页码）
_NON_BODY_LABELS = {
    "number",
    "header",
    "footer",
    "page_header",
    "page_footer",
    "page_number",
}
# 图片/表格区域标签
_FIGURE_LABELS = {"figure", "image", "picture"}
_TABLE_LABELS = {"table"}


# ============================================================
# 前端结果类型
# ============================================================


@dataclass
class OcrLine:
    """单条 OCR 文本行（图像像素坐标）。"""

    bbox_px: list[float]  # [x0, y0, x1, y1]
    text: str
    confidence: float = 1.0


@dataclass
class OcrRegion:
    """版面分析区域（图像像素坐标）。"""

    bbox_px: list[float]  # [x0, y0, x1, y1]
    label: str = "text"


@dataclass
class OcrPageResult:
    """单页 OCR + 版面分析结果（图像像素坐标）。"""

    width_px: int
    height_px: int
    lines: list[OcrLine] = field(default_factory=list)
    regions: list[OcrRegion] = field(default_factory=list)


class OcrFrontend(Protocol):
    """OCR 前端接口（可注入，便于用 stub 测试 source 感知逻辑）。"""

    def recognize(self, image: Any) -> OcrPageResult:
        """对一张图像做 OCR + 版面分析。"""
        ...


# ============================================================
# OcrExtractor
# ============================================================


class OcrExtractor:
    """扫描件 PDF / 图片 → ExtractionResult。

    编排：渲染为图像 → 前端 OCR + 版面分析 → 构建 pipeline elements（带 layout_label
    与估算字号）→ SplitPipeline（source_type="ocr"）→ element→BlockNode 映射。
    """

    source_type: SourceType = SourceType.OCR

    def __init__(
        self,
        *,
        pipeline: SplitPipeline | None = None,
        frontend: OcrFrontend | None = None,
        split_stages: SplitStages | None = None,
        dpi: int = OCR_DPI,
        min_confidence: float = _DEFAULT_MIN_CONFIDENCE,
        debug_dir: str | Path | None = None,
    ):
        self._pipeline = pipeline or SplitPipeline(
            stages=split_stages or default_split_stages(),
            debug_dir=str(debug_dir) if debug_dir else None,
        )
        self._frontend = frontend  # None → 延迟到 PaddleOcrFrontend
        self._dpi = dpi
        self._min_confidence = min_confidence

    @property
    def frontend(self) -> OcrFrontend:
        if self._frontend is None:
            self._frontend = PaddleOcrFrontend()
        return self._frontend

    def extract(
        self,
        source: SourceLike,
        *,
        options: Any = None,
    ) -> ExtractionResult:
        """解析扫描件/图片 → ExtractionResult。"""
        opts = _normalize_options(options)
        dpi = int(opts.get("dpi", self._dpi))
        pages = opts.get("pages")

        # 1. 渲染为图像（图片直接用；PDF 逐页栅格化）
        images = self._render_to_images(source, dpi=dpi, pages=pages)
        if not images:
            return ExtractionResult(
                content=[],
                metadata=self._metadata(source, page_count=0),
                toc_entries=None,
            )

        # 2. 逐页 OCR + 构建 pipeline elements
        pages_data: list[tuple[list[dict], PipelineContext]] = []
        for page_index, img in images:
            try:
                page_result = self.frontend.recognize(img)
            except Exception as e:
                _logger.warning("OCR 第 %d 页失败，跳过: %s", page_index, e)
                pages_data.append(([], PipelineContext(page_index=page_index, source_type="ocr")))
                continue
            ctx, elements = self._build_page(page_result, page_index, dpi)
            pages_data.append((elements, ctx))

        # 3. 跑分流管线
        processed_pages = self._pipeline.run(pages_data)

        # 4. 元素 → BlockNode
        blocks, toc_entries = elements_to_blocks(
            processed_pages, source_type=SourceType.OCR
        )

        # 5. 元数据
        body_ctx = pages_data[0][1] if pages_data else None
        custom: dict[str, Any] = {}
        if body_ctx and body_ctx.body_font_size is not None:
            custom["body_font_size"] = body_ctx.body_font_size
        if body_ctx and body_ctx.body_font:
            custom["body_font"] = body_ctx.body_font

        return ExtractionResult(
            content=blocks,
            metadata=self._metadata(source, page_count=len(images), custom=custom),
            toc_entries=toc_entries or None,
        )

    # ---------- 坐标与元素构建 ----------

    def _build_page(
        self,
        page: OcrPageResult,
        page_index: int,
        dpi: int,
    ) -> tuple[PipelineContext, list[dict]]:
        """把单页 OCR 结果转为 (PipelineContext, elements)。

        坐标从图像像素换算为 PDF 点（pt = px × 72 / dpi），与版面过滤坐标系一致。
        """
        scale = 72.0 / dpi
        width_pt = page.width_px * scale
        height_pt = page.height_px * scale

        # 区域也换算到 pt
        regions_pt = [
            OcrRegion(
                bbox_px=[v * scale for v in r.bbox_px],
                label=r.label,
            )
            for r in page.regions
            if len(r.bbox_px) >= 4
        ]

        elements: list[dict] = []
        order_index = 0

        for line in page.lines:
            if len(line.bbox_px) < 4:
                continue
            text = (line.text or "").strip()
            if not text:
                continue

            # 像素 → pt
            bx0, by0, bx1, by1 = [v * scale for v in line.bbox_px[:4]]
            bbox_pt = [round(bx0, 2), round(by0, 2), round(bx1, 2), round(by1, 2)]

            # 该行中心落入哪个区域 → layout_label
            cx = (bx0 + bx1) / 2
            cy = (by0 + by1) / 2
            layout_label = _region_label_at(regions_pt, cx, cy)

            # footer/number/header 区域 → 不进 content
            if layout_label in _NON_BODY_LABELS:
                continue

            # 估算字号 = bbox 高度（pt）
            estimated_size = round(max(by1 - by0, 0.1), 2)

            span = {
                "text": text,
                "font": "OCR",
                "size": estimated_size,
                "bbox": bbox_pt,
                "flags": 0,
                "confidence": float(line.confidence),
            }
            elements.append(
                {
                    "type": None,
                    "label": "ocr_line",
                    "level": None,
                    "text": text,
                    "markdown": text,
                    "bbox": bbox_pt,
                    "order_index": order_index,
                    "page_index": page_index,
                    "confidence": float(line.confidence),
                    "low_confidence": bool(line.confidence < self._min_confidence),
                    "style": {
                        "font": "OCR",
                        "size": estimated_size,
                        "bold": False,
                        "italic": False,
                        "flags": 0,
                        "layout_label": layout_label,
                    },
                    "spans": [span],
                }
            )
            order_index += 1

        # figure / table 区域 → 占位元素（无文本时）
        elements.extend(
            self._placeholder_elements(regions_pt, page_index, len(elements))
        )

        # 排序：(y_top, x0)
        elements.sort(key=lambda e: (e["bbox"][1], e["bbox"][0]))
        for i, elem in enumerate(elements):
            elem["order_index"] = i

        ctx = PipelineContext(
            page_width=width_pt,
            page_height=height_pt,
            page_index=page_index,
            source_type="ocr",
        )
        return ctx, elements

    @staticmethod
    def _placeholder_elements(
        regions_pt: list[OcrRegion],
        page_index: int,
        start_order: int,
    ) -> list[dict]:
        """figure/table 区域 → image/table 占位元素。"""
        out: list[dict] = []
        order = start_order
        for r in regions_pt:
            if r.label in _FIGURE_LABELS:
                out.append(
                    {
                        "type": "image",
                        "text": f"p{page_index}_fig_{order}",
                        "bbox": [round(v, 2) for v in r.bbox_px],
                        "order_index": order,
                        "page_index": page_index,
                    }
                )
                order += 1
            elif r.label in _TABLE_LABELS:
                out.append(
                    {
                        "type": "table",
                        "text": "",
                        "markdown": "",
                        "bbox": [round(v, 2) for v in r.bbox_px],
                        "order_index": order,
                        "page_index": page_index,
                        "style": {},
                        "spans": [],
                        "table_rows": [[]],
                    }
                )
                order += 1
        return out

    # ---------- 图像渲染 ----------

    def _render_to_images(
        self,
        source: SourceLike,
        *,
        dpi: int,
        pages: list[int] | None,
    ) -> list[tuple[int, Any]]:
        """把源（图片或 PDF）转为 [(page_index, image), ...]。

        image 为 PIL.Image（PaddleOCR 前端可消费）。
        """
        try:
            from PIL import Image
        except ImportError as e:
            raise InvalidSourceError("Pillow 未安装，无法处理图像输入") from e

        # 已是 PIL Image（直通）
        if hasattr(source, "save") and hasattr(source, "convert"):
            return [(0, source.convert("RGB"))]

        # bytes / 图片文件 / PDF 文件
        if isinstance(source, (bytes, bytearray)):
            return self._images_from_bytes(bytes(source), dpi=dpi, pages=pages)

        path = Path(source)
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {path}")

        suffix = path.suffix.lower()
        if suffix == ".pdf":
            data = path.read_bytes()
            return self._images_from_bytes(data, dpi=dpi, pages=pages)
        # 图片
        img = Image.open(io.BytesIO(path.read_bytes()))
        img.load()
        return [(0, img.convert("RGB"))]

    @staticmethod
    def _images_from_bytes(
        data: bytes,
        *,
        dpi: int,
        pages: list[int] | None,
    ) -> list[tuple[int, Any]]:
        from PIL import Image
        import pymupdf

        # 先尝试当 PDF 打开
        try:
            doc = pymupdf.open(stream=data, filetype="pdf")
        except Exception as e:
            # 不是 PDF → 当图片
            doc = None
            _logger.debug("非 PDF，尝试按图像打开: %s", e)

        if doc is None or (doc is not None and doc.is_pdf is False):
            try:
                img = Image.open(io.BytesIO(data))
                img.load()
                return [(0, img.convert("RGB"))]
            except Exception as e:
                raise InvalidSourceError(f"无法识别的图像/PDF 输入: {e}") from e

        total = len(doc)
        target = [p for p in (pages or range(total)) if 0 <= p < total]
        images: list[tuple[int, Any]] = []
        zoom = dpi / 72.0
        matrix = pymupdf.Matrix(zoom, zoom)
        for page_idx in target:
            page = doc[page_idx]
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            img.load()
            images.append((page_idx, img.convert("RGB")))
        doc.close()
        return images

    @staticmethod
    def _metadata(
        source: SourceLike,
        *,
        page_count: int,
        custom: dict[str, Any] | None = None,
    ) -> DocumentMetadata:
        source_file = None
        if isinstance(source, (str, os.PathLike)):
            source_file = Path(source).name
        return DocumentMetadata(
            source_type=SourceType.OCR,
            source_file=source_file,
            page_count=page_count,
            custom=custom or {},
        )


# ============================================================
# PaddleOCR 前端（3.x API；可选依赖，懒加载）
# ============================================================


class PaddleOcrFrontend:
    """基于 PaddleOCR 3.x 的真实前端（文本 + 版面分析）。

    模型在首次 ``recognize`` 时加载（可能触发下载）。结果解析防御性处理，
    适配 3.x 的 result 对象（含 dt_polys/rec_texts/rec_scores 与版面 polygons/labels）。
    """

    def __init__(
        self,
        *,
        lang: str = "ch",
        use_textline_orientation: bool = True,
        layout_threshold: float = 0.5,
        layout_nms: bool = True,
    ):
        self._lang = lang
        self._use_textline_orientation = use_textline_orientation
        self._layout_threshold = layout_threshold
        self._layout_nms = layout_nms
        self._ocr_engine = None
        self._layout_engine = None

    def _ensure_engines(self) -> None:
        if self._ocr_engine is not None:
            return
        try:
            from paddleocr import LayoutDetection, PaddleOCR
        except ImportError as e:
            raise InvalidSourceError(
                "paddleocr 未安装，请安装 ocr extra: pip install 'document2chunk[ocr]'"
            ) from e

        self._ocr_engine = PaddleOCR(
            use_textline_orientation=self._use_textline_orientation,
            lang=self._lang,
        )
        self._layout_engine = LayoutDetection(
            threshold=self._layout_threshold,
            layout_nms=self._layout_nms,
        )

    def recognize(self, image: Any) -> OcrPageResult:
        self._ensure_engines()
        width_px, height_px = _image_size(image)

        # 文本识别
        lines: list[OcrLine] = []
        ocr_res = self._ocr_engine.predict(image)
        for text_b, conf, poly in _iter_ocr_texts(ocr_res):
            bbox = _poly_to_bbox(poly)
            if bbox:
                lines.append(OcrLine(bbox_px=bbox, text=text_b, confidence=conf))

        # 版面分析
        regions: list[OcrRegion] = []
        layout_res = self._layout_engine.predict(image)
        for label, poly in _iter_layout_regions(layout_res):
            bbox = _poly_to_bbox(poly)
            if bbox:
                regions.append(OcrRegion(bbox_px=bbox, label=label))

        return OcrPageResult(
            width_px=width_px, height_px=height_px, lines=lines, regions=regions
        )


# ============================================================
# PaddleOCR 结果解析工具（防御性，适配 3.x）
# ============================================================


def _image_size(image: Any) -> tuple[int, int]:
    try:
        return int(image.width), int(image.height)
    except AttributeError:
        pass
    # ndarray (H, W, ...)
    try:
        return int(image.shape[1]), int(image.shape[0])
    except AttributeError:
        return 0, 0


def _result_json(res: Any) -> Any:
    """从 paddleocr 3.x result 对象取可遍历的 dict/序列。"""
    if res is None:
        return None
    # 直接是 list
    if isinstance(res, list):
        return res
    # 单个 result 对象
    for attr in ("json", "dict"):
        val = getattr(res, attr, None)
        if val is not None:
            return val
    return res


def _iter_ocr_texts(ocr_res: Any):
    """yield (text, confidence, polygon) from PaddleOCR.predict 结果。"""
    data = _result_json(ocr_res)
    items = data[0] if isinstance(data, list) and data else data
    if items is None:
        return

    # 3.x dict: rec_texts / rec_scores / dt_polys
    if isinstance(items, dict):
        texts = items.get("rec_texts") or items.get("texts") or []
        scores = items.get("rec_scores") or items.get("scores") or []
        polys = items.get("dt_polys") or items.get("polys") or items.get("rec_polys") or []
        for i, poly in enumerate(polys):
            text = texts[i] if i < len(texts) else ""
            conf = float(scores[i]) if i < len(scores) else 1.0
            yield text, conf, poly
        return

    # 2.x 兼容：[[bbox, (text, conf)], ...]
    for entry in items or []:
        try:
            if isinstance(entry, list) and len(entry) >= 2:
                poly = entry[0]
                text_conf = entry[1]
                yield text_conf[0], float(text_conf[1]), poly
        except Exception:
            continue


def _iter_layout_regions(layout_res: Any):
    """yield (label, polygon) from LayoutDetection.predict 结果。"""
    data = _result_json(layout_res)
    items = data[0] if isinstance(data, list) and data else data
    if not isinstance(items, dict):
        return

    # 常见键：polygons + labels；或 res.boxes（含 label + coordinate）
    polys = items.get("polygons") or []
    labels = items.get("labels") or items.get("rec_labels") or []

    if polys:
        for i, poly in enumerate(polys):
            label = labels[i] if i < len(labels) else "text"
            yield _norm_label(label), poly
        return

    # res.boxes 路径
    res = items.get("res", {})
    boxes = res.get("boxes", []) if isinstance(res, dict) else []
    for box in boxes:
        label = _norm_label(box.get("label", "text"))
        coord = box.get("coordinate") or box.get("bbox") or box.get("poly")
        yield label, coord


def _norm_label(label: Any) -> str:
    if not label:
        return "text"
    s = str(label).strip().lower()
    # 归一化常见别名
    aliases = {
        "title": "title",
        "text": "text",
        "figure": "figure",
        "image": "figure",
        "picture": "figure",
        "table": "table",
        "footer": "footer",
        "header": "header",
        "number": "number",
        "page_number": "number",
        "page_header": "header",
        "page_footer": "footer",
    }
    return aliases.get(s, s)


def _poly_to_bbox(poly: Any) -> list[float] | None:
    """多边形（4 点或 4 数）→ [x0, y0, x1, y1]。"""
    if poly is None:
        return None
    try:
        if len(poly) >= 4 and not isinstance(poly[0], (list, tuple)):
            # 已是 [x0,y0,x1,y1]
            return [float(poly[0]), float(poly[1]), float(poly[2]), float(poly[3])]
        xs = [float(p[0]) for p in poly]
        ys = [float(p[1]) for p in poly]
        if not xs:
            return None
        return [min(xs), min(ys), max(xs), max(ys)]
    except Exception:
        return None


# ============================================================
# options 工具
# ============================================================


def _normalize_options(options: Any) -> dict[str, Any]:
    if options is None:
        return {}
    if isinstance(options, dict):
        return options
    result: dict[str, Any] = {}
    for key in ("dpi", "pages", "min_confidence"):
        val = getattr(options, key, None)
        if val is not None:
            result[key] = val
    return result


def _region_label_at(regions: Sequence[OcrRegion], cx: float, cy: float) -> Optional[str]:
    """点 (cx,cy) 落入的第一个区域 label（无则 None）。"""
    for r in regions:
        b = r.bbox_px
        if len(b) >= 4 and b[0] <= cx <= b[2] and b[1] <= cy <= b[3]:
            return r.label
    return None


__all__ = [
    "OcrExtractor",
    "OcrFrontend",
    "PaddleOcrFrontend",
    "OcrPageResult",
    "OcrLine",
    "OcrRegion",
]
