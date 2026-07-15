"""parsers.markdown（markdown→IR）单元测试 —— 无需 OCR 服务。

运行：PYTHONPATH=src python tests/test_markdown_parser.py
"""

from __future__ import annotations

from collections import Counter

from document2chunk.ir import (
    FormulaNode,
    HeadingNode,
    ImageNode,
    ListNode,
    ParagraphNode,
    SourceType,
    TableNode,
)
from document2chunk.parsers.markdown import markdown_to_blocks

SRC = SourceType.OCR


def _types(blocks):
    return dict(Counter(type(b).__name__ for b in blocks))


def test_headings():
    blocks = markdown_to_blocks("# H1\n\n## H2\n\n### H3\n\n###### H6")
    levels = [(b.level, b.text) for b in blocks]
    assert levels == [(1, "H1"), (2, "H2"), (3, "H3"), (6, "H6")], levels
    print("OK test_headings")


def test_paragraph_and_inline_cleanup():
    blocks = markdown_to_blocks("含 **加粗**、`代码`、~~删~~ 的段落。")
    assert len(blocks) == 1 and isinstance(blocks[0], ParagraphNode)
    assert blocks[0].text == "含 加粗、代码、删 的段落。", blocks[0].text
    print("OK test_paragraph_and_inline_cleanup")


def test_table():
    md = "| 项目 | 金额 |\n| --- | ---: |\n| 收入 | 100 |\n| 成本 | 40 |"
    blocks = markdown_to_blocks(md)
    assert len(blocks) == 1 and isinstance(blocks[0], TableNode)
    t = blocks[0]
    assert len(t.rows) == 3  # 表头 + 2 数据行
    assert t.rows[0].is_header is True
    assert [c.blocks[0].text for c in t.rows[0].cells] == ["项目", "金额"]
    assert [c.blocks[0].text for c in t.rows[1].cells] == ["收入", "100"]
    print("OK test_table")


def test_list_nested():
    md = "- 一\n- 二\n  - 嵌套\n- 三"
    blocks = markdown_to_blocks(md)
    assert len(blocks) == 1 and isinstance(blocks[0], ListNode)
    lst = blocks[0]
    assert lst.ordered is False
    assert [it.level for it in lst.items] == [0, 0, 1, 0], [it.level for it in lst.items]
    assert [it.blocks[0].text for it in lst.items] == ["一", "二", "嵌套", "三"]
    print("OK test_list_nested")


def test_ordered_list():
    blocks = markdown_to_blocks("1. 甲\n2. 乙\n3. 丙")
    assert isinstance(blocks[0], ListNode) and blocks[0].ordered is True
    assert len(blocks[0].items) == 3
    print("OK test_ordered_list")


def test_block_formula():
    blocks = markdown_to_blocks("$$\nE = mc^2\n$$")
    assert len(blocks) == 1 and isinstance(blocks[0], FormulaNode)
    assert blocks[0].latex == "E = mc^2", blocks[0].latex
    print("OK test_block_formula")


def test_inline_formula_oneline():
    blocks = markdown_to_blocks("$$E = mc^2$$")
    assert isinstance(blocks[0], FormulaNode) and blocks[0].latex == "E = mc^2"
    print("OK test_inline_formula_oneline")


def test_image_line():
    blocks = markdown_to_blocks("![示意图](images/fig1.png)")
    assert len(blocks) == 1 and isinstance(blocks[0], ImageNode)
    assert blocks[0].image_id == "images/fig1.png"
    assert blocks[0].alt == "示意图"
    print("OK test_image_line")


def test_mixed_document():
    md = """# 报告

导语段落。

## 一、概况

| A | B |
| - | - |
| 1 | 2 |

- 要点一
- 要点二

$$x^2$$

![图](f.png)

收尾。
"""
    blocks = markdown_to_blocks(md)
    t = _types(blocks)
    assert t.get("HeadingNode", 0) == 2
    assert t.get("TableNode", 0) == 1
    assert t.get("ListNode", 0) == 1
    assert t.get("FormulaNode", 0) == 1
    assert t.get("ImageNode", 0) == 1
    assert t.get("ParagraphNode", 0) >= 2
    print("OK test_mixed_document", t)


def main():
    test_headings()
    test_paragraph_and_inline_cleanup()
    test_table()
    test_list_nested()
    test_ordered_list()
    test_block_formula()
    test_inline_formula_oneline()
    test_image_line()
    test_mixed_document()
    print("\nALL MARKDOWN PARSER TESTS PASSED")


if __name__ == "__main__":
    main()
