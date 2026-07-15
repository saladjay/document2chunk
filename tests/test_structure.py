"""structure-builder 测试。"""

from __future__ import annotations

from document2chunk.ir import (
    BlockType,
    DocumentMetadata,
    ExtractionResult,
    HeadingNode,
    ListNode,
    ListItemNode,
    LogicalDocument,
    ParagraphNode,
    SourceType,
    TocEntry,
    TocNode,
)
from document2chunk.structure import assemble, build_section_tree


def _meta():
    return DocumentMetadata(source_type=SourceType.DOCX)


def test_no_headings():
    result = ExtractionResult(
        content=[
            ParagraphNode(id="b1", text="p1"),
            ParagraphNode(id="b2", text="p2"),
        ],
        metadata=_meta(),
    )
    doc = assemble(result)
    assert len(doc.section_tree.subsections) == 0
    assert doc.section_tree.block_ids == ["b1", "b2"]
    assert doc.block_to_section == {"b1": "sec_root", "b2": "sec_root"}


def test_nested():
    result = ExtractionResult(
        content=[
            HeadingNode(id="b1", level=1, text="H1"),
            ParagraphNode(id="b2", text="p"),
            HeadingNode(id="b3", level=2, text="H2"),
            ParagraphNode(id="b4", text="p"),
        ],
        metadata=_meta(),
    )
    doc = assemble(result)
    assert len(doc.section_tree.subsections) == 1
    h1 = doc.section_tree.subsections[0]
    assert h1.title == "H1" and h1.level == 1
    assert h1.block_ids == ["b2"]
    assert len(h1.subsections) == 1
    h2 = h1.subsections[0]
    assert h2.title == "H2" and h2.level == 2
    assert h2.block_ids == ["b4"]
    assert doc.block_to_section["b4"] == h2.id


def test_level_jump():
    # H1 -> H3：H3 应挂到 H1 下（跳过 H2）
    result = ExtractionResult(
        content=[
            HeadingNode(id="b1", level=1, text="H1"),
            HeadingNode(id="b2", level=3, text="H3"),
            ParagraphNode(id="b3", text="p"),
        ],
        metadata=_meta(),
    )
    doc = assemble(result)
    h1 = doc.section_tree.subsections[0]
    assert h1.level == 1
    assert len(h1.subsections) == 1
    assert h1.subsections[0].level == 3


def test_heading_level_bounds():
    # IR 层强制 level ∈ [1,9]；构造 >9 会抛 ValidationError（逼 extractor 先 clamp）。
    # 这里验证边界值 1 与 9 都能正常建树。
    for lvl in (1, 9):
        result = ExtractionResult(
            content=[HeadingNode(id="b1", level=lvl, text="X")],
            metadata=_meta(),
        )
        doc = assemble(result)
        assert doc.section_tree.subsections[0].level == lvl


def test_toc_calibration():
    # 标题被误判为 level=2，TOC 信号校准为 1
    result = ExtractionResult(
        content=[HeadingNode(id="b1", level=2, text="第一章")],
        metadata=_meta(),
        toc_entries=[TocEntry(text="第一章", level=1)],
    )
    doc = assemble(result)
    assert doc.content[0].level == 1
    assert doc.section_tree.subsections[0].level == 1


def test_keep_toc_default_off():
    result = ExtractionResult(
        content=[ParagraphNode(id="b1", text="p")],
        metadata=_meta(),
        toc_entries=[TocEntry(text="t", level=1)],
    )
    doc = assemble(result)
    assert not any(isinstance(b, TocNode) for b in doc.content)


def test_keep_toc_on():
    result = ExtractionResult(
        content=[ParagraphNode(id="b1", text="p")],
        metadata=_meta(),
        toc_entries=[TocEntry(text="t", level=1, page=3)],
    )
    doc = assemble(result, keep_toc=True)
    tocs = [b for b in doc.content if isinstance(b, TocNode)]
    assert len(tocs) == 1
    assert tocs[0].entries[0] == {"text": "t", "level": 1, "page": 3}


def test_build_section_tree_skips_tocnode():
    root, b2s = build_section_tree(
        [
            HeadingNode(id="b1", level=1, text="H1"),
            TocNode(id="t1", entries=[{"text": "x"}]),
            ParagraphNode(id="b2", text="p"),
        ]
    )
    h1 = root.subsections[0]
    assert h1.block_ids == ["b2"]  # TocNode 未计入
    assert "t1" not in b2s


def test_assemble_consumes_pdf_content():
    # assemble 必须能消费任意来源的 content（解耦关键）
    result = ExtractionResult(
        content=[
            HeadingNode(id="b1", level=1, text="Ch"),
            ListNode(
                id="b2",
                items=[ListItemNode(id="i1", level=0, blocks=[])],
            ),
        ],
        metadata=DocumentMetadata(source_type=SourceType.PDF),
    )
    doc = assemble(result)
    assert isinstance(doc, LogicalDocument)
    assert doc.metadata.source_type == SourceType.PDF
    assert doc.section_tree.subsections[0].block_ids == ["b2"]


if __name__ == "__main__":
    for fn in [
        test_no_headings,
        test_nested,
        test_level_jump,
        test_heading_level_bounds,
        test_toc_calibration,
        test_keep_toc_default_off,
        test_keep_toc_on,
        test_build_section_tree_skips_tocnode,
        test_assemble_consumes_pdf_content,
    ]:
        fn()
        print(f"ok: {fn.__name__}")
    print("ALL STRUCTURE TESTS PASSED")
