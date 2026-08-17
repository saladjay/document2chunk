"""DocxExtractor —— .docx → ExtractionResult（lxml 直读 + 统一 postprocess）。"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import List, Optional

from document2chunk.extractors.docx.package_reader import PackageReader
from document2chunk.extractors.docx.parser import DocumentParser
from document2chunk.extractors.docx.styles import StyleRegistry
from document2chunk.ir import (
    BlockNode,
    DocumentMetadata,
    ExtractionResult,
    ListNode,
    ParagraphNode,
    SourceType,
    TocEntry,
)


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


class DocxExtractor:
    """可编辑 .docx 提取器。"""

    source_type: SourceType = SourceType.DOCX

    def extract(
        self,
        source,
        *,
        options=None,
        heuristic_headings: bool = False,
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

        # 统一后处理（第三路汇合，designs/009）：DOCX 无页几何，页相关步骤天然 no-op；
        # 收益是 calibrate_levels（doc_title 字号比 + 栈式定级）与 split_attachments。
        from document2chunk.postprocess import postprocess
        main_content, attach_segments = postprocess(
            blocks, metadata,
            toc_entries=toc_entries if toc_entries else None,
            page_geometry=None,
            use_height_fallback=False,
            body_font_size=_body_font_size(blocks),
        )

        result = ExtractionResult(
            content=main_content,
            metadata=metadata,
            toc_entries=toc_entries if toc_entries else None,
        )
        for seg in attach_segments:
            result.attachments.append(ExtractionResult(content=seg, metadata=_meta(
                custom={"is_attachment": True})))
        return result
