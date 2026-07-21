"""pdf-extractor: 可编辑 PDF → ExtractionResult。

实现依据：
- specs/pdf-extractor/spec.md（行为契约 + element→BlockNode 映射表）
- designs/002（复用地图）、designs/003 §6/§8/§9（源码契约与映射）

组件：
- :class:`PyMuPDFSpanExtractor`：PDF → 行级 element + 表格（pdfplumber 优先、
  PyMuPDF 兜底双引擎）+ 图片信息。``_bbox_overlap``/``_sort_key``/``_clean_cell``
  已提到模块级（消除原 parser_pymupdf 的内嵌闭包反模式）。
- :class:`PdfExtractor`：编排 span 提取 → 线性 :class:`Pipeline`（5 stage）→
  element→BlockNode 映射 → 全文档 postprocess → :class:`ExtractionResult`。
  ``extract`` 不产出完整 LogicalDocument、不调 structure-builder（INTEGRATION §2）。
- :func:`detect_pdf_type`：可编辑性检测（scanned/mixed → 路由到 ocr-extractor）。
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Union

from document2chunk.exceptions import InvalidSourceError
from document2chunk.extractors._mapping import elements_to_blocks
from document2chunk.pipeline.pdf_detect import DetectResult, detect_pdf_type
from document2chunk.ir import (
    BlockNode,
    DocumentMetadata,
    ExtractionResult,
    Provenance,
    SourceType,
    TocEntry,
)
from document2chunk.pipeline import (
    PipelineContext,
    Pipeline,
    load_layout_data,
    pdf_pipeline,
)
from document2chunk.pipeline.config import IMAGE_FORMAT, IMAGE_MIN_AREA
from document2chunk.pipeline.stages.layout_filter import layout_boxes_for_page

_logger = logging.getLogger("document2chunk.extractors.pdf")

SourceLike = Union[str, Path, bytes]


# ============================================================
# 模块级工具（消除原 parser_pymupdf 的内嵌闭包反模式）
# ============================================================


def _has_font_token(font: str | None, *tokens: str) -> bool:
    """字体名是否包含任一特征词（大小写不敏感）。"""
    if not font:
        return False
    lower = font.lower()
    return any(tok.lower() in lower for tok in tokens)


def _bbox_overlap(b1: list[float], b2: list[float], threshold: float = 0.5) -> bool:
    """两个 bbox 交集面积占 b1 面积的比例是否 > threshold。"""
    ix0 = max(b1[0], b2[0])
    iy0 = max(b1[1], b2[1])
    ix1 = min(b1[2], b2[2])
    iy1 = min(b1[3], b2[3])

    if ix0 >= ix1 or iy0 >= iy1:
        return False

    inter_area = (ix1 - ix0) * (iy1 - iy0)
    line_area = (b1[2] - b1[0]) * (b1[3] - b1[1])
    if line_area <= 0:
        return False

    return inter_area / line_area > threshold


def _sort_key(elem: dict) -> tuple[float, float]:
    """主排序键：(y_top, x0)。"""
    bbox = elem.get("bbox") or [0, 0, 0, 0]
    return (bbox[1], bbox[0])


def _clean_cell(cell: Any) -> str:
    """清理单元格内容：首尾空白 + 内部换行折叠为单空格。"""
    if not cell:
        return ""
    return " ".join(str(cell).split()).strip()


def _table_to_markdown(table_data: list[list[Any]]) -> str:
    """二维表格 → Markdown 表格字符串（第一行为表头）。"""
    if not table_data or not table_data[0]:
        return ""

    md_lines: list[str] = []
    header = [_clean_cell(c) for c in table_data[0]]
    md_lines.append("| " + " | ".join(header) + " |")
    md_lines.append("|" + "|".join([" --- " for _ in header]) + "|")

    for row in table_data[1:]:
        cleaned_row = [_clean_cell(c) for c in row]
        md_lines.append("| " + " | ".join(cleaned_row) + " |")

    return "\n".join(md_lines)


# ---- 表格校验（designs/004 §3.2：修封面/排版被误判为表）----
TABLE_MIN_ROWS = 2
TABLE_MIN_COLS = 2


def _validate_table(
    bbox: list[float],
    rows: list,
    layout_boxes: list[tuple[str, list[float]]] | None,
    page_w: float = 0.0,
    page_h: float = 0.0,
) -> bool:
    """判定检出的「表」是否为真表（保留）；否则降级为文字（designs/004 §3.2 + R11）。

    保留条件（满足其一）：
    - layout-backed：存在 ``table`` 版面框与该 bbox 显著重叠；或
    - 启发式：行数 ≥ ``TABLE_MIN_ROWS``、列数 ≥ ``TABLE_MIN_COLS``、非全空。

    拒绝条件：覆盖 >50% 页面且无 layout 确认 → HTML 版 PDF 整页布局误判为表格
    （真表格极少覆盖过半首页，首页多为标题+正文）。
    """
    if layout_boxes:
        for label, lbox in layout_boxes:
            if label == "table" and _bbox_overlap(bbox, lbox, 0.3):
                return True
    if page_w and page_h and len(bbox) >= 4:
        coverage = ((bbox[2] - bbox[0]) * (bbox[3] - bbox[1])) / (page_w * page_h)
        if coverage > 0.5:
            return False  # 整页布局误判（HTML 版 PDF 通病），降级为文字
    if rows and len(rows) >= TABLE_MIN_ROWS:
        max_cols = max((len(r or []) for r in rows), default=0)
        if max_cols >= TABLE_MIN_COLS and any(
            c and str(c).strip() for r in rows for c in (r or [])
        ):
            return True
    return False


def _open_pymupdf(source: SourceLike):
    """打开 PDF（path 或 bytes），返回 (doc, source)。"""
    import pymupdf

    if isinstance(source, (bytes, bytearray)):
        return pymupdf.open(stream=bytes(source), filetype="pdf")
    return pymupdf.open(str(Path(source)))


# ============================================================
# 原始页结构
# ============================================================


@dataclass
class RawPage:
    """单页原始提取结果（未跑管线）。"""

    page_index: int
    width: float
    height: float
    elements: list[dict] = field(default_factory=list)
    image_infos: list[dict] = field(default_factory=list)


# ============================================================
# PyMuPDFSpanExtractor
# ============================================================


class PyMuPDFSpanExtractor:
    """从可编辑 PDF 提取行级 element + 表格 + 图片信息。

    表格双引擎（designs/003 §6、ADR001）：pdfplumber 优先（更准确），
    PyMuPDF 原生 ``find_tables`` 兜底；与表格重叠 >50% 的文本行被排除。
    pdfplumber 全文只开一次（修原「逐页重开 PDF」反模式）。
    """

    @property
    def source_type(self) -> str:
        return "pymupdf"

    def extract(
        self,
        source: SourceLike,
        *,
        pages: list[int] | None = None,
        extract_tables: bool = True,
        extract_images: bool = True,
        image_dir: str | Path | None = None,
        layout_data=None,
    ) -> list[RawPage]:
        """提取所有目标页的原始 element + 图片信息。

        Args:
            source: PDF 路径或二进制。
            pages: 指定页码（0-based），None 表示全部。
            extract_tables: 是否提取表格。
            extract_images: 是否提取图片信息（image_dir 提供时另存文件）。
            image_dir: 图片保存目录；None 则不落盘（IR 不引用磁盘路径）。
            layout_data: 版面分析结果（可选）；用于表格校验（designs/004 §3.2）。
        """
        doc = _open_pymupdf(source)
        total = len(doc)
        target_pages = [p for p in (pages or range(total)) if 0 <= p < total]

        # pdfplumber 表格：全文开一次，预取每页表格（primary 引擎）
        tables_by_page: dict[int, list[tuple[list[float], list]]] = {}
        if extract_tables:
            tables_by_page = self._extract_tables_pdfplumber(source, target_pages)

        raw_pages: list[RawPage] = []
        for page_idx in target_pages:
            page = doc[page_idx]
            layout_boxes = layout_boxes_for_page(
                layout_data, page_idx, page.rect.width, page.rect.height
            )
            elements = self._extract_raw_elements(
                page, page_idx, tables_by_page.get(page_idx, []), extract_tables, layout_boxes
            )
            image_infos = (
                self._extract_page_images(doc, page, page_idx, image_dir)
                if extract_images
                else []
            )
            raw_pages.append(
                RawPage(
                    page_index=page_idx,
                    width=page.rect.width,
                    height=page.rect.height,
                    elements=elements,
                    image_infos=image_infos,
                )
            )

        doc.close()
        return raw_pages

    # ---------- 表格（pdfplumber primary） ----------

    def _extract_tables_pdfplumber(
        self,
        source: SourceLike,
        target_pages: list[int],
    ) -> dict[int, list[tuple[list[float], list]]]:
        """用 pdfplumber 提取每页表格 → {page_idx: [(bbox, rows), ...]}。

        pdfplumber 缺失时静默降级（交由 PyMuPDF 兜底）。
        """
        try:
            import pdfplumber
        except ImportError:
            _logger.debug("pdfplumber 未安装，表格提取走 PyMuPDF 兜底")
            return {}

        result: dict[int, list[tuple[list[float], list]]] = {}
        try:
            if isinstance(source, (bytes, bytearray)):
                stream: Any = io.BytesIO(bytes(source))
                pdf = pdfplumber.open(stream)
            else:
                pdf = pdfplumber.open(str(Path(source)))
            try:
                for page_idx in target_pages:
                    if page_idx >= len(pdf.pages):
                        continue
                    pl_page = pdf.pages[page_idx]
                    for table in pl_page.find_tables():
                        rows = table.extract() or []
                        result.setdefault(page_idx, []).append(
                            (list(table.bbox), rows)
                        )
            finally:
                pdf.close()
        except Exception as e:
            _logger.warning("pdfplumber 表格提取失败，改用 PyMuPDF 兜底: %s", e)
            return {}

        return result

    # ---------- 原始元素 ----------

    def _extract_raw_elements(
        self,
        page,
        page_idx: int,
        pdfplumber_tables: list[tuple[list[float], list]],
        extract_tables: bool,
        layout_boxes: list[tuple[str, list[float]]] | None = None,
    ) -> list[dict]:
        """从 PyMuPDF page 提取行级 element + 表格（纯提取，不含分类逻辑）。

        表格先经 :func:`_validate_table` 校验（designs/004 §3.2）：误判的表（封面/排版）
        被降级——不当表，其文本行正常提取，不被排除。
        """
        elements: list[dict] = []
        order_index = 0
        pw, ph = page.rect.width, page.rect.height  # R11：整页表格覆盖判据

        # 1. pdfplumber 表格（primary）— 校验后保留
        for bbox, rows in pdfplumber_tables:
            if not _validate_table(bbox, rows, layout_boxes, pw, ph):
                continue  # 降级：不当表，其文字正常提取
            markdown = _table_to_markdown(rows)
            elements.append(
                self._make_table_element(bbox, markdown, rows, page_idx, order_index)
            )
            order_index += 1

        # 2. PyMuPDF 兜底：仅当本页尚无表格时尝试（同样校验）
        if extract_tables and not any(e.get("type") == "table" for e in elements):
            try:
                tables = page.find_tables()
                if tables.tables:
                    for table in tables.tables:
                        rows = table.extract() or []
                        tbbox = list(table.bbox)
                        if not _validate_table(tbbox, rows, layout_boxes, pw, ph):
                            continue  # 降级
                        markdown = _table_to_markdown(rows) or ""
                        elements.append(
                            self._make_table_element(
                                tbbox, markdown, rows, page_idx, order_index
                            )
                        )
                        order_index += 1
            except Exception as e:
                _logger.warning("PyMuPDF 表格提取失败 (page %d): %s", page_idx, e)

        # 已检测到的表格区域（用于文本去重）
        table_bboxes = [e["bbox"] for e in elements if e.get("type") == "table"]

        # 3. 文本块（排除与表格重叠 >50% 的行）
        for block in page.get_text("dict")["blocks"]:
            if block["type"] != 0:  # 跳过图片块等非文本块
                continue

            for line in block["lines"]:
                spans = line["spans"]
                if not spans:
                    continue

                line_bbox = [
                    min(s["bbox"][0] for s in spans),
                    min(s["bbox"][1] for s in spans),
                    max(s["bbox"][2] for s in spans),
                    max(s["bbox"][3] for s in spans),
                ]

                # 与表格区域重叠 >50% → 跳过（表格已覆盖该文本）
                if any(_bbox_overlap(line_bbox, tb) for tb in table_bboxes):
                    continue

                full_text = "".join(s["text"] for s in spans).strip()
                if not full_text:
                    continue

                font_sizes = [s["size"] for s in spans if s["text"].strip()]
                fonts = [s["font"] for s in spans if s["text"].strip()]
                if not font_sizes:
                    continue

                # 代表字号：取最大值（标题通常字号更大）
                max_size = max(font_sizes)
                # 代表字体：取第一个非空字体
                primary_font = fonts[0] if fonts else "Unknown"
                # 代表 flags：取第一个 span（AutoLevel bold 位用）
                primary_flags = spans[0]["flags"]

                is_bold = any(_has_font_token(f, "Bold") for f in fonts)
                is_italic = any(
                    _has_font_token(f, "Italic", "Oblique") for f in fonts
                )

                element = {
                    "type": None,  # 未分类，由 ClassificationStage 填充
                    "label": "text_line",
                    "level": None,
                    "text": full_text,
                    "markdown": full_text,
                    "bbox": [round(v, 2) for v in line_bbox],
                    "order_index": order_index,
                    "page_index": page_idx,
                    "style": {
                        "font": primary_font,
                        "size": round(max_size, 2),
                        "bold": is_bold,
                        "italic": is_italic,
                        "flags": primary_flags,
                    },
                    "spans": [
                        {
                            "text": s["text"],
                            "font": s["font"],
                            "size": round(s["size"], 2),
                            "bbox": [round(v, 2) for v in s["bbox"]],
                            "origin": [round(v, 2) for v in s["origin"]],
                            "flags": s["flags"],
                        }
                        for s in spans
                    ],
                }
                elements.append(element)
                order_index += 1

        # 4. 排序：(y_top, x0) + y_overlap 气泡修正
        elements.sort(key=_sort_key)
        self._reorder_overlapping(elements)

        # 重新赋 order_index
        for i, elem in enumerate(elements):
            elem["order_index"] = i

        return elements

    @staticmethod
    def _make_table_element(
        bbox: list[float],
        markdown: str,
        rows: list,
        page_idx: int,
        order_index: int,
    ) -> dict:
        """构造表格 element（携带 table_rows 供 IR 映射重建 TableNode）。"""
        return {
            "type": "table",
            "text": markdown,
            "markdown": markdown,
            "bbox": [round(float(v), 2) for v in bbox],
            "order_index": order_index,
            "page_index": page_idx,
            "style": {},
            "spans": [],
            "table_rows": rows,
        }

    @staticmethod
    def _reorder_overlapping(elements: list[dict]) -> None:
        """相邻元素若 y 方向重叠且 x0 顺序颠倒，则交换（处理标题/章节号提取顺序）。"""
        i = 0
        while i < len(elements) - 1:
            curr = elements[i]
            next_elem = elements[i + 1]
            curr_bbox = curr.get("bbox", [])
            next_bbox = next_elem.get("bbox", [])

            if len(curr_bbox) >= 4 and len(next_bbox) >= 4:
                y_overlap = min(curr_bbox[3], next_bbox[3]) - max(
                    curr_bbox[1], next_bbox[1]
                )
                if y_overlap > 0 and curr_bbox[0] > next_bbox[0]:
                    elements[i], elements[i + 1] = elements[i + 1], elements[i]
                    i = max(0, i - 1)  # 回退一步重新检查
                    continue
            i += 1

    # ---------- 图片 ----------

    def _extract_page_images(
        self,
        doc,
        page,
        page_idx: int,
        image_dir: str | Path | None,
    ) -> list[dict]:
        """提取当前页图片信息（bbox/image_id/width/height/format）。

        image_dir 提供时另存图片文件；否则仅收集信息（IR 不引用磁盘路径，
        ImageNode.data 默认不填，specs/pdf-extractor §4）。
        """
        try:
            image_info_list = page.get_image_info()
        except Exception:
            return []

        # xref → bytes 缓存（仅 image_dir 落盘时需要）
        save = image_dir is not None
        xref_cache: dict[int, bytes] = {}
        xref_to_image_list = page.get_images(full=True) if save else []
        if save:
            for img_item in xref_to_image_list:
                xref = img_item[0]
                if xref not in xref_cache:
                    try:
                        base_image = doc.extract_image(xref)
                        if base_image:
                            xref_cache[xref] = base_image["image"]
                    except Exception:
                        pass

        page_images: list[dict] = []
        img_idx = 0
        for img_info in image_info_list:
            bbox = img_info.get("bbox")
            if not bbox or len(bbox) < 4:
                continue

            x0, y0, x1, y1 = bbox
            width = x1 - x0
            height = y1 - y0
            if width * height < IMAGE_MIN_AREA:
                continue

            img_idx += 1
            image_id = f"p{page_idx}_{img_idx}"
            fmt = IMAGE_FORMAT
            filename = ""

            if save:
                xref = img_info.get("xref", 0)
                img_bytes = xref_cache.get(xref) if xref else None
                if not img_bytes and xref:
                    try:
                        base_image = doc.extract_image(xref)
                        if base_image:
                            img_bytes = base_image["image"]
                            fmt = base_image.get("ext", IMAGE_FORMAT) or IMAGE_FORMAT
                            xref_cache[xref] = img_bytes
                    except Exception:
                        pass
                if not img_bytes:
                    try:
                        import pymupdf

                        clip = pymupdf.Rect(x0, y0, x1, y1)
                        pix = page.get_pixmap(clip=clip, dpi=150)
                        img_bytes = pix.tobytes(IMAGE_FORMAT)
                    except Exception:
                        continue
                if img_bytes:
                    filename = f"{image_id}.{fmt}"
                    try:
                        (Path(image_dir) / filename).write_bytes(img_bytes)
                    except Exception:
                        filename = ""

            page_images.append(
                {
                    "bbox": [round(v, 2) for v in bbox],
                    "image_id": image_id,
                    "filename": filename,
                    "xref": img_info.get("xref", 0),
                    "width": round(width, 2),
                    "height": round(height, 2),
                    "format": fmt,
                }
            )

        return page_images


# ============================================================
# PdfExtractor
# ============================================================


class PdfExtractor:
    """可编辑 PDF → ExtractionResult。

    编排：PyMuPDFSpanExtractor 提取 → 线性 Pipeline（5 stage）→ element→BlockNode 映射
    → 全文档 postprocess（跨页合并/噪声/定级/附件）。scanned/mixed 不在本 extractor
    范围（fast-fail，路由到 ocr-extractor）。
    """

    source_type: SourceType = SourceType.PDF

    def __init__(
        self,
        *,
        pipeline: Pipeline | None = None,
        layout_jsonl: str | Path | None = None,
        extract_images: bool = True,
        image_dir: str | Path | None = None,
        debug_dir: str | Path | None = None,
        skip_detect: bool = False,
    ):
        """
        Args:
            pipeline: 自定义 Pipeline；None 则用 :func:`pdf_pipeline` 默认（5 stage）。
            layout_jsonl: 版面分析 JSONL 路径（PaddleOCR LayoutDetection 输出）。
            extract_images: 是否提取图片信息。
            image_dir: 图片落盘目录（None 不落盘）。
            debug_dir: 管线调试目录（每 stage 写 {NN}_{name}.json）。
            skip_detect: 跳过可编辑性检测（已知 editable 时省开销）。
        """
        if pipeline is not None:
            self._pipeline = pipeline
        else:
            self._pipeline = pdf_pipeline(
                str(layout_jsonl) if layout_jsonl else None,
                debug_dir=str(debug_dir) if debug_dir else None,
            )
        self._layout_jsonl = layout_jsonl
        self._extract_images = extract_images
        self._image_dir = image_dir
        self._debug_dir = debug_dir
        self._skip_detect = skip_detect
        self._span_extractor = PyMuPDFSpanExtractor()

    def extract(
        self,
        source: SourceLike,
        *,
        options: Any = None,
    ) -> ExtractionResult:
        """解析可编辑 PDF → ExtractionResult（content + metadata + toc_entries）。"""
        # 可选配置覆盖
        opts = _normalize_options(options)
        skip_detect = opts.get("skip_detect", self._skip_detect)
        extract_images = opts.get("extract_images", self._extract_images)
        image_dir = opts.get("image_dir", self._image_dir)
        pages = opts.get("pages")

        # 1. 可编辑性检测（scanned/mixed → fast-fail）
        if not skip_detect:
            detect: DetectResult = detect_pdf_type(source, pages=_detect_pages(pages))
            if detect.pdf_type in ("scanned", "mixed"):
                raise InvalidSourceError(
                    f"PDF 为 {detect.pdf_type}（非可编辑），应路由到 ocr-extractor"
                )

        # 2. 版面分析数据（可选）
        layout_data = None
        layout_jsonl = opts.get("layout_jsonl", self._layout_jsonl)
        if layout_jsonl:
            layout_data = load_layout_data(layout_jsonl)

        # 3. 原始提取
        raw_pages = self._span_extractor.extract(
            source,
            pages=pages,
            extract_tables=True,
            extract_images=extract_images,
            image_dir=image_dir,
            layout_data=layout_data,
        )
        if not raw_pages:
            return ExtractionResult(
                content=[],
                metadata=self._metadata(source, page_count=0),
                toc_entries=None,
            )

        # 4. 构造管线输入
        pages_data: list[tuple[list[dict], PipelineContext]] = []
        image_infos_by_page: dict[int, list[dict]] = {}
        for raw in raw_pages:
            ctx = PipelineContext(
                page_width=raw.width,
                page_height=raw.height,
                page_index=raw.page_index,
                layout_data=layout_data,
                image_infos=raw.image_infos,
                source_type="pdf",
            )
            pages_data.append((raw.elements, ctx))
            image_infos_by_page[raw.page_index] = raw.image_infos

        # 5. 跑分流管线
        try:
            processed_pages = self._pipeline.run(pages_data)
        except Exception as e:
            _logger.error("管线执行失败: %s", e)
            raise

        # 6. 元素 → BlockNode
        blocks, toc_entries = elements_to_blocks(
            processed_pages,
            source_type=SourceType.PDF,
            image_infos_by_page=image_infos_by_page,
        )

        # 7. 元数据（正文基准提升到 custom）
        body_ctx = pages_data[0][1]
        custom: dict[str, Any] = {}
        if body_ctx.body_font:
            custom["body_font"] = body_ctx.body_font
        if body_ctx.body_font_size is not None:
            custom["body_font_size"] = body_ctx.body_font_size

        metadata = self._metadata(source, page_count=len(raw_pages), custom=custom)

        # 8. 全文档后处理（两路共用：噪声过滤 + 跨页合并 + 标题定级 + 附件拆分，designs/009）
        from document2chunk.postprocess import postprocess
        page_geometry = {}
        for _elems, ctx in pages_data:
            if hasattr(ctx, "page_width") and hasattr(ctx, "page_height") and hasattr(ctx, "page_index"):
                page_geometry[ctx.page_index] = (ctx.page_width, ctx.page_height)
        pp_log: list = []
        main_content, attach_segments = postprocess(
            blocks, metadata,
            toc_entries=toc_entries,
            page_geometry=page_geometry,
            layout_data=layout_data,
            use_height_fallback=False,  # edited-PDF：居中 + 高度比（DOC_TITLE_EDITED_RATIO）
            _log=pp_log,
        )
        if self._debug_dir:
            import json as _json, os as _os
            _os.makedirs(str(self._debug_dir), exist_ok=True)
            with open(_os.path.join(str(self._debug_dir), "postprocess_log.json"), "w", encoding="utf-8") as f:
                _json.dump(pp_log, f, ensure_ascii=False, indent=2)

        result = ExtractionResult(content=main_content, metadata=metadata, toc_entries=toc_entries or None)
        for seg in attach_segments:
            result.attachments.append(ExtractionResult(content=seg, metadata=self._metadata(
                source, page_count=0, custom={"is_attachment": True})))
        return result

    @staticmethod
    def _metadata(
        source: SourceLike,
        *,
        page_count: int,
        custom: dict[str, Any] | None = None,
    ) -> DocumentMetadata:
        source_file = None
        if not isinstance(source, (bytes, bytearray)):
            source_file = Path(source).name
        return DocumentMetadata(
            source_type=SourceType.PDF,
            source_file=source_file,
            page_count=page_count,
            custom=custom or {},
        )


# ============================================================
# options 工具
# ============================================================


def _normalize_options(options: Any) -> dict[str, Any]:
    """把 options（dict / 对象 / None）归一为 dict。"""
    if options is None:
        return {}
    if isinstance(options, dict):
        return options
    # 对象：取已知属性
    result: dict[str, Any] = {}
    for key in (
        "skip_detect",
        "extract_images",
        "image_dir",
        "pages",
        "layout_jsonl",
        "debug_dir",
    ):
        val = getattr(options, key, None)
        if val is not None:
            result[key] = val
    return result


def _detect_pages(pages: list[int] | None) -> list[int] | None:
    """detect_pdf_type 的页码采样：多页时只查前几页以省开销。"""
    if pages is None:
        return None
    return sorted(pages)[:3]


__all__ = [
    "PdfExtractor",
    "PyMuPDFSpanExtractor",
    "RawPage",
    "detect_pdf_type",
    "DetectResult",
]
