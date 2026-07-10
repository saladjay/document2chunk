"""DocxExtractor —— .docx → ExtractionResult（lxml 直读，provenance 全 None）。"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from document2chunk.extractors.docx.package_reader import PackageReader
from document2chunk.extractors.docx.parser import DocumentParser
from document2chunk.extractors.docx.styles import StyleRegistry
from document2chunk.ir import (
    DocumentMetadata,
    ExtractionResult,
    SourceType,
    TocEntry,
)


class InvalidDocxError(Exception):
    """无效的 .docx 文件。"""


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

        metadata = DocumentMetadata(
            source_type=SourceType.DOCX,
            source_file=source_file,
            title=core.get("title"),
            author=core.get("author"),
            created=core.get("created"),
            modified=core.get("modified"),
        )

        return ExtractionResult(
            content=blocks,
            metadata=metadata,
            toc_entries=toc_entries if toc_entries else None,
        )
