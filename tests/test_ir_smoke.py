"""ir-model 契约冒烟测试 —— 同时充当节点用法的活样例。

运行：
    PYTHONPATH=src python -m pytest tests/test_ir_smoke.py
    或：PYTHONPATH=src python tests/test_ir_smoke.py
"""

from __future__ import annotations

import json

from document2chunk.ir import (
    DocumentMetadata,
    ExtractionResult,
    HeadingNode,
    HyperlinkNode,
    ImageNode,
    ListNode,
    ListItemNode,
    LogicalDocument,
    ParagraphNode,
    Provenance,
    RunNode,
    RunProperties,
    SectionNode,
    SourceType,
    TableCellNode,
    TableNode,
    TableRowNode,
    TocEntry,
)


def _build_doc() -> LogicalDocument:
    # PDF 段落：RunNode 带 provenance（span→RunNode，bbox 落在 provenance）
    para = ParagraphNode(
        id="block_000002",
        runs=[
            RunNode(
                id="run_000001",
                text="正文内容",
                style=RunProperties(font="SimSun", font_size=12.0),
                provenance=Provenance(
                    source_type=SourceType.PDF, page_index=0, bbox=[72.0, 100.0, 300.0, 116.0]
                ),
            ),
            HyperlinkNode(id="run_000002", target="https://example.com", runs=[]),
        ],
        text="正文内容",
        provenance=Provenance(source_type=SourceType.PDF, page_index=0),
    )

    # 表格：含嵌套段落（验证 BlockNode 递归嵌套）
    table = TableNode(
        id="block_000003",
        provenance=Provenance(source_type=SourceType.PDF, page_index=0),
        rows=[
            TableRowNode(
                id="row_1",
                is_header=True,
                cells=[
                    TableCellNode(id="cell_1", blocks=[
                        ParagraphNode(id="block_000004", text="表头A")
                    ]),
                    TableCellNode(id="cell_2", blocks=[
                        ParagraphNode(id="block_000005", text="表头B")
                    ]),
                ],
            )
        ],
    )

    # 列表
    lst = ListNode(
        id="block_000006",
        ordered=True,
        items=[
            ListItemNode(id="item_1", level=0, blocks=[
                ParagraphNode(id="block_000007", text="第一项")
            ]),
        ],
    )

    # docx 标题：provenance 为 None（D6）
    h1 = HeadingNode(id="block_000001", level=1, text="第一章")

    img = ImageNode(
        id="block_000008",
        image_id="rId1",
        format="png",
        width_emu=914400,
        height_emu=914400,
        alt="示意图",
        provenance=Provenance(source_type=SourceType.PDF, page_index=0),
    )

    root = SectionNode(
        id="sec_root",
        title="ROOT",
        level=0,
        subsections=[
            SectionNode(
                id="sec_000001",
                title="第一章",
                level=1,
                heading_node_id="block_000001",
                block_ids=["block_000002", "block_000003"],
                subsections=[
                    SectionNode(
                        id="sec_000002",
                        title="1.1 节",
                        level=2,
                        block_ids=["block_000006"],
                        parent_id="sec_000001",
                    ),
                ],
                parent_id="sec_root",
            ),
        ],
    )

    return LogicalDocument(
        metadata=DocumentMetadata(
            title="样例",
            source_type=SourceType.PDF,
            source_file="sample.pdf",
            page_count=1,
        ),
        content=[h1, para, table, lst, img],
        section_tree=root,
        block_to_section={
            "block_000001": "sec_000001",
            "block_000002": "sec_000001",
            "block_000003": "sec_000001",
            "block_000006": "sec_000002",
        },
    )


def test_roundtrip():
    doc = _build_doc()
    payload = doc.model_dump_json(exclude_none=True)
    restored = LogicalDocument.model_validate_json(payload)

    # 顶层结构保持
    assert restored.metadata.title == "样例"
    assert len(restored.content) == 5
    # 判别联合正确还原
    assert isinstance(restored.content[0], HeadingNode)
    assert isinstance(restored.content[1], ParagraphNode)
    assert isinstance(restored.content[2], TableNode)
    # span→RunNode，provenance.bbox 保留
    run = restored.content[1].runs[0]
    assert isinstance(run, RunNode)
    assert run.provenance is not None
    assert run.provenance.bbox == [72.0, 100.0, 300.0, 116.0]
    # docx 标题无 provenance（序列化 exclude_none 后字段不存在）
    assert restored.content[0].provenance is None


def test_nested_block_lookup():
    doc = _build_doc()
    # 嵌套块（表格单元格内）能被深度查到
    nested = doc.get_block("block_000004")
    assert nested is not None
    assert nested.id == "block_000004"
    # 顶层块
    assert doc.get_block("block_000001").level == 1
    # 不存在
    assert doc.get_block("nope") is None


def test_section_traversal():
    doc = _build_doc()
    sec2 = doc.get_section("sec_000002")
    assert sec2 is not None
    assert sec2.level == 2
    assert sec2.parent_id == "sec_000001"
    # 章节总数（root + 2）
    assert len(list(doc.iter_sections())) == 3
    # 块总数（5 顶层 + 2 嵌套表格单元格 + 1 列表项 = 8）
    assert len(list(doc.iter_blocks())) == 8


def test_discriminated_parse():
    payload = '{"id":"b","type":"heading","level":3,"text":"x"}'
    doc = LogicalDocument(
        metadata=DocumentMetadata(),
        content=[json.loads(payload)],  # dict 由 LogicalDocument 校验为 HeadingNode
        section_tree=SectionNode(id="r", title="r", level=0),
    )
    assert isinstance(doc.content[0], HeadingNode)
    assert doc.content[0].level == 3


def test_extraction_result():
    # extractor 产出 ExtractionResult（content + metadata + toc_entries）
    result = ExtractionResult(
        content=[HeadingNode(id="block_000001", level=1, text="第一章")],
        metadata=DocumentMetadata(source_type=SourceType.DOCX),
        toc_entries=[TocEntry(text="第一章", level=1, page=None)],
    )
    payload = result.model_dump_json(exclude_none=True)
    restored = ExtractionResult.model_validate_json(payload)
    assert restored.metadata.source_type == SourceType.DOCX
    assert isinstance(restored.content[0], HeadingNode)
    assert restored.toc_entries[0].level == 1


if __name__ == "__main__":
    test_roundtrip()
    test_nested_block_lookup()
    test_section_traversal()
    test_discriminated_parse()
    test_extraction_result()
    print("ALL SMOKE TESTS PASSED")
