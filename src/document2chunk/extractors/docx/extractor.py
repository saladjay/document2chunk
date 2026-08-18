"""DocxExtractor —— .docx → ExtractionResult（lxml 直读 + 统一 postprocess）。"""

from __future__ import annotations

import logging
from collections import Counter
from pathlib import Path
from typing import Iterator, List, Optional

from document2chunk.extractors.docx.package_reader import PackageReader
from document2chunk.extractors.docx.parser import DocumentParser
from document2chunk.extractors.docx import notes
from document2chunk.extractors.docx.styles import StyleRegistry
from document2chunk.ir import (
    BlockNode,
    DocumentMetadata,
    ExtractionResult,
    ImageNode,
    ListNode,
    ParagraphNode,
    SourceType,
    TableNode,
)

_logger = logging.getLogger(__name__)


class InvalidDocxError(Exception):
    """无效的 .docx 文件。"""


def _body_font_size(blocks: List[BlockNode]) -> Optional[float]:
    """正文基准字号：全部段落 run 字号（pt）的众数（仿 BodyAnalysisStage）。

    遍历顶层段落 + 列表项内段落；表格内文字不参与（表内字号常异于正文）。
    """
    sizes: List[float] = []

    def walk(bs: List[BlockNode]) -> None:
        for b in bs:
            if isinstance(b, ParagraphNode):
                for r in b.runs or []:
                    fs = getattr(getattr(r, "style", None), "font_size", None)
                    if fs:
                        sizes.append(round(float(fs), 1))
            elif isinstance(b, ListNode):
                for item in b.items:
                    walk(item.blocks)

    walk(blocks)
    return Counter(sizes).most_common(1)[0][0] if sizes else None


def _iter_images(blocks) -> Iterator[ImageNode]:
    """递归收集块序列中的 ImageNode（含表格/列表嵌套）。"""
    for b in blocks:
        if isinstance(b, ImageNode):
            yield b
        elif isinstance(b, TableNode):
            for row in b.rows:
                for cell in row.cells:
                    yield from _iter_images(cell.blocks)
        elif isinstance(b, ListNode):
            for item in b.items:
                yield from _iter_images(item.blocks)


def _export_media(reader: PackageReader, blocks, image_dir) -> None:
    """仅落盘被引用媒体，zip 内原名；失败 WARN 跳过（对齐 PDF 路，阶段B §4.7）。"""
    out = Path(image_dir)
    try:
        out.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        _logger.warning("docx 媒体目录创建失败 %s: %s", out, e)
        return
    for img in _iter_images(blocks):
        if not img.image_id:
            continue
        info = reader.media_info_for_rel(img.image_id)
        if info is None:
            continue
        name, data, _ext = info
        try:
            (out / name).write_bytes(data)
        except OSError as e:
            _logger.warning("docx 媒体落盘失败 %s: %s", name, e)


class DocxExtractor:
    """可编辑 .docx 提取器。"""

    source_type: SourceType = SourceType.DOCX

    def extract(
        self,
        source,
        *,
        options=None,
        heuristic_headings: bool = False,
        image_dir=None,
    ) -> ExtractionResult:
        reader = PackageReader(source)

        doc_elem = reader.document_element()
        if doc_elem is None:
            raise InvalidDocxError("缺少 word/document.xml，不是有效的 .docx")

        registry = StyleRegistry()
        registry.load(reader.styles_element())

        parser = DocumentParser(
            registry,
            numbering_elem=reader.numbering_element(),
            reader=reader,
            heuristic_headings=heuristic_headings,
        )
        blocks, toc_entries = parser.parse(doc_elem)

        # 媒体落盘（仅被引用媒体；IR 不引磁盘路径）
        if image_dir is not None:
            _export_media(reader, blocks, image_dir)

        core = reader.core_properties()
        source_file = Path(source).name if isinstance(source, (str, Path)) else None

        def _meta(custom: Optional[dict] = None) -> DocumentMetadata:
            return DocumentMetadata(
                source_type=SourceType.DOCX,
                source_file=source_file,
                title=core.get("title"),
                author=core.get("author"),
                created=core.get("created"),
                modified=core.get("modified"),
                custom=custom or {},
            )

        metadata = _meta()

        # 页眉文本 → metadata.custom（不进正文，阶段B §4.8）
        header_lines = [
            " ".join("".join(h.itertext()).split()) for h in reader.header_elements()
        ]
        header_text = " / ".join(x for x in header_lines if x)[:200]
        if header_text:
            metadata.custom["docx"] = {"header_text": header_text}

        # 统一后处理（第三路汇合，designs/009）：DOCX 无页几何，页相关步骤天然 no-op；
        # 收益是 calibrate_levels（doc_title 字号比 + 栈式定级）与 split_attachments。
        from document2chunk.postprocess import postprocess
        pp_log: list = []
        main_content, attach_segments = postprocess(
            blocks, metadata,
            toc_entries=toc_entries if toc_entries else None,
            page_geometry=None,
            use_height_fallback=False,
            body_font_size=_body_font_size(blocks),
            _log=pp_log,
        )

        # 尾注/脚注内容：正文末尾集中。必须在 postprocess 之后——
        # 若在之前追加，split_attachments 会把尾注划进文档末尾的附件段（设计 §4.5）
        main_content = (
            main_content
            + notes.parse_notes(reader, "endnote")
            + notes.parse_notes(reader, "footnote")
        )

        result = ExtractionResult(
            content=main_content,
            metadata=metadata,
            toc_entries=toc_entries if toc_entries else None,
        )
        for seg in attach_segments:
            result.attachments.append(ExtractionResult(content=seg, metadata=_meta(
                custom={"is_attachment": True})))

        debug_dir = None
        if isinstance(options, dict):
            debug_dir = options.get("debug_dir")
        else:
            debug_dir = getattr(options, "debug_dir", None)
        if debug_dir:
            import json as _json
            import os as _os
            _os.makedirs(str(debug_dir), exist_ok=True)
            with open(_os.path.join(str(debug_dir), "postprocess_log.json"), "w", encoding="utf-8") as f:
                _json.dump(pp_log, f, ensure_ascii=False, indent=2)
        return result
