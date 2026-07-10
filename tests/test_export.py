"""export 测试。"""

from __future__ import annotations

from document2chunk.export import to_json, to_jsonl, to_markdown, to_plain_text
from document2chunk.ir import (
    DocumentMetadata,
    ExtractionResult,
    HeadingNode,
    ImageNode,
    ListNode,
    ListItemNode,
    LogicalDocument,
    ParagraphNode,
    SourceType,
    TableCellNode,
    TableNode,
    TableRowNode,
)
from document2chunk.structure import assemble


def _doc() -> LogicalDocument:
    table = TableNode(
        id="b3",
        rows=[
            TableRowNode(
                id="r1",
                is_header=True,
                cells=[
                    TableCellNode(id="c1", blocks=[ParagraphNode(id="b4", text="A")]),
                    TableCellNode(id="c2", blocks=[ParagraphNode(id="b5", text="B")]),
                ],
            ),
            TableRowNode(
                id="r2",
                cells=[
                    TableCellNode(id="c3", blocks=[ParagraphNode(id="b6", text="1")]),
                    TableCellNode(id="c4", blocks=[ParagraphNode(id="b7", text="2")]),
                ],
            ),
        ],
    )
    lst = ListNode(
        id="b8",
        ordered=False,
        items=[ListItemNode(id="i1", level=0, blocks=[ParagraphNode(id="b9", text="项一")])],
    )
    img = ImageNode(id="b10", image_id="rId1", alt="示意图")
    result = ExtractionResult(
        content=[
            HeadingNode(id="b1", level=1, text="标题一"),
            ParagraphNode(id="b2", text="正文段落。"),
            table,
            lst,
            img,
        ],
        metadata=DocumentMetadata(source_type=SourceType.DOCX, title="文档"),
    )
    return assemble(result)


def test_json_roundtrip():
    doc = _doc()
    payload = to_json(doc)
    restored = LogicalDocument.model_validate_json(payload)
    assert restored.metadata.title == "文档"
    assert len(restored.content) == 5
    assert isinstance(restored.content[0], HeadingNode)
    assert isinstance(restored.content[2], TableNode)


def test_markdown():
    md = to_markdown(_doc())
    assert "# 标题一" in md
    assert "正文段落。" in md
    assert "| A | B |" in md
    assert "| --- | --- |" in md
    assert "- 项一" in md
    assert "![示意图](rId1)" in md


def test_markdown_front_matter():
    md = to_markdown(_doc(), include_metadata=True)
    assert md.startswith("---")
    assert "title: 文档" in md


def test_plain_text():
    txt = to_plain_text(_doc())
    assert "标题一" in txt
    assert "正文段落。" in txt
    assert "A\tB" in txt  # 表格行用 tab 连接
    assert "项一" in txt


def test_jsonl():
    lines = to_jsonl(_doc()).splitlines()
    assert len(lines) == 5  # 每 content 块一行


if __name__ == "__main__":
    for fn in [
        test_json_roundtrip,
        test_markdown,
        test_markdown_front_matter,
        test_plain_text,
        test_jsonl,
    ]:
        fn()
        print(f"ok: {fn.__name__}")
    print("ALL EXPORT TESTS PASSED")
