"""pdf-extractor 契约冒烟测试（specs/pdf-extractor §7 验收）。

运行：
    PYTHONPATH=src python tests/test_pdf_extractor.py

覆盖：
- 可编辑 PDF → ExtractionResult（source_type=pdf、provenance、RunNode 映射）
- 多页页码检测（≥70% 页命中 → page_number 不进 content）
- 目录页 → toc_entries 信号、不进 content
- scanned PDF → detect_pdf_type scanned + PdfExtractor fast-fail
- element→TableNode 映射（含 table_rows）
- ExtractionResult 往返；可选 structure.assemble → LogicalDocument 往返
"""

from __future__ import annotations

from collections import Counter

from document2chunk.exceptions import InvalidSourceError
from document2chunk.extractors._mapping import elements_to_blocks
from document2chunk.pipeline.pdf_detect import detect_pdf_type
from document2chunk.extractors.pdf import PdfExtractor, PyMuPDFSpanExtractor
from document2chunk.ir import (
    ExtractionResult,
    HeadingNode,
    ImageNode,
    ParagraphNode,
    SourceType,
    TableNode,
)

from _fixtures import make_multipage_pdf, make_scanned_pdf_bytes, make_simple_pdf, make_toc_pdf


# ---------- 可编辑 PDF 基本契约 ----------


def test_editable_pdf_to_result():
    pdf = make_simple_pdf()
    result = PdfExtractor(skip_detect=True, extract_images=False).extract(pdf)

    assert result.metadata.source_type == SourceType.PDF
    assert result.metadata.page_count == 1
    # 正文基准提升到 custom
    assert "body_font_size" in result.metadata.custom

    types = Counter(type(b).__name__ for b in result.content)
    assert types["HeadingNode"] >= 1, types  # 大标题 + 二级标题
    assert types["ParagraphNode"] >= 1, types

    # 标题/段落均带 pdf provenance + page_index
    for b in result.content:
        assert b.provenance is not None
        assert b.provenance.source_type == SourceType.PDF
        assert b.provenance.page_index == 0

    # RunNode 映射：heading/段落的 runs 来自 span，provenance.bbox 保留
    heading = next(b for b in result.content if isinstance(b, HeadingNode))
    assert len(heading.runs) >= 1
    run = heading.runs[0]
    assert run.provenance is not None
    assert run.provenance.bbox is not None and len(run.provenance.bbox) == 4

    print(f"OK test_editable_pdf_to_result (types={dict(types)})")


def test_extraction_result_roundtrip():
    pdf = make_simple_pdf()
    result = PdfExtractor(skip_detect=True, extract_images=False).extract(pdf)
    payload = result.model_dump_json(exclude_none=True)
    r2 = ExtractionResult.model_validate_json(payload)
    assert len(r2.content) == len(result.content)
    assert r2.metadata.source_type == SourceType.PDF
    # 判别联合正确还原
    assert any(isinstance(b, HeadingNode) for b in r2.content)
    print("OK test_extraction_result_roundtrip")


# ---------- 多页页码检测 ----------


def test_page_number_excluded_multipage():
    pdf = make_multipage_pdf(n=4)
    result = PdfExtractor(skip_detect=True, extract_images=False).extract(pdf)
    # 4 页都有底部页码 "1".."4" → PageNumberDetection 标记，不进 content
    texts = [getattr(b, "text", "") for b in result.content]
    for n in ("1", "2", "3", "4"):
        # 页码纯数字不应作为独立段落出现
        assert n not in texts, f"page number {n!r} leaked into content"
    print("OK test_page_number_excluded_multipage")


# ---------- 目录页 ----------


def test_toc_signal_not_in_content():
    pdf = make_toc_pdf()
    result = PdfExtractor(skip_detect=True, extract_images=False).extract(pdf)
    # 目录条目作 toc_entries 信号，不进 content
    assert result.toc_entries, "expected toc_entries signal"
    # content 中不应出现点线引导的目录条目
    for b in result.content:
        txt = getattr(b, "text", "")
        assert ".........." not in txt, f"toc entry leaked: {txt!r}"
    print(f"OK test_toc_signal_not_in_content (toc_entries={len(result.toc_entries)})")


# ---------- scanned PDF 路由 ----------


def test_scanned_pdf_detect_and_fastfail():
    pdf = make_scanned_pdf_bytes()
    det = detect_pdf_type(pdf, pages=[0])
    assert det.pdf_type == "scanned", det.pdf_type
    # PdfExtractor 应 fast-fail（路由到 ocr-extractor）
    try:
        PdfExtractor(extract_images=False).extract(pdf)
        assert False, "expected InvalidSourceError"
    except InvalidSourceError:
        pass
    print("OK test_scanned_pdf_detect_and_fastfail")


# ---------- 表格映射（单测：table_rows → TableNode）----------


def test_table_mapping():
    pages = [
        [
            {
                "type": "table",
                "text": "| A | B |",
                "markdown": "| A | B |",
                "bbox": [72, 200, 300, 260],
                "order_index": 0,
                "page_index": 0,
                "style": {},
                "spans": [],
                "table_rows": [["A", "B"], ["1", "2"]],
            }
        ]
    ]
    blocks, toc = elements_to_blocks(pages, source_type=SourceType.PDF)
    assert len(blocks) == 1
    table = blocks[0]
    assert isinstance(table, TableNode)
    assert len(table.rows) == 2
    assert table.rows[0].is_header is True
    assert table.rows[1].is_header is False
    assert [c.blocks[0].text for c in table.rows[0].cells] == ["A", "B"]
    assert [c.blocks[0].text for c in table.rows[1].cells] == ["1", "2"]
    assert table.provenance.source_type == SourceType.PDF
    print("OK test_table_mapping")


def test_table_mapping_from_markdown_fallback():
    """无 table_rows 时解析 markdown 兜底。"""
    pages = [
        [
            {
                "type": "table",
                "text": "",
                "markdown": "| A | B |\n| --- | --- |\n| 1 | 2 |",
                "bbox": [72, 200, 300, 260],
                "order_index": 0,
                "page_index": 0,
                "style": {},
                "spans": [],
            }
        ]
    ]
    blocks, _ = elements_to_blocks(pages, source_type=SourceType.PDF)
    assert len(blocks) == 1 and isinstance(blocks[0], TableNode)
    assert [c.blocks[0].text for c in blocks[0].rows[1].cells] == ["1", "2"]
    print("OK test_table_mapping_from_markdown_fallback")


# ---------- LogicalDocument（可选：依赖 structure.assemble）----------


def test_logical_document_via_assemble():
    """若 structure.assemble 可用，验证 ExtractionResult → LogicalDocument 往返。"""
    try:
        from document2chunk.structure import assemble
    except Exception:
        print("SKIP test_logical_document_via_assemble (structure.assemble 不可用)")
        return

    pdf = make_multipage_pdf(n=3)
    result = PdfExtractor(skip_detect=True, extract_images=False).extract(pdf)
    doc = assemble(result)
    assert doc.metadata.source_type == SourceType.PDF
    assert len(doc.section_tree.subsections) >= 1 or doc.section_tree.block_ids
    # 序列化往返
    payload = doc.model_dump_json(exclude_none=True)
    from document2chunk.ir import LogicalDocument

    doc2 = LogicalDocument.model_validate_json(payload)
    assert len(doc2.content) == len(doc.content)
    assert doc2.section_tree is not None
    print(
        f"OK test_logical_document_via_assemble "
        f"(sections={len(list(doc.iter_sections()))}, blocks={len(doc.content)})"
    )


# ---------- span 提取器（双引擎结构）----------


def test_span_extractor_structure():
    pdf = make_simple_pdf()
    raw = PyMuPDFSpanExtractor().extract(pdf, extract_images=False)
    assert len(raw) == 1
    assert raw[0].page_index == 0
    assert len(raw[0].elements) >= 3
    # 每个 text element 有 spans + style（含 flags）
    for e in raw[0].elements:
        if e.get("type") is None:  # text line
            assert "flags" in e["style"]
            assert e["spans"]
    print("OK test_span_extractor_structure")


# ---------- runner ----------


def main():
    test_editable_pdf_to_result()
    test_extraction_result_roundtrip()
    test_page_number_excluded_multipage()
    test_toc_signal_not_in_content()
    test_scanned_pdf_detect_and_fastfail()
    test_table_mapping()
    test_table_mapping_from_markdown_fallback()
    test_span_extractor_structure()
    test_logical_document_via_assemble()
    print("\nALL PDF EXTRACTOR TESTS PASSED")


if __name__ == "__main__":
    main()
