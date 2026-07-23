from mineru2doc.markdown_parser import parse_markdown
from mineru2doc.model import TEXT, TABLE, IMAGE, EQUATION, LIST_T


def test_headings_and_paragraph():
    md = "# 标题一\n\n正文段落。\n\n## 子标题\n\n更多正文。\n"
    blocks = parse_markdown(md)
    assert [b.type for b in blocks] == [TEXT, TEXT, TEXT, TEXT]
    assert blocks[0].level == 1 and blocks[0].text == "标题一"
    assert blocks[1].level is None
    assert blocks[2].level == 2


def test_table_html():
    blocks = parse_markdown("<table><tr><td>1</td></tr></table>")
    assert blocks[0].type == TABLE
    assert "<table>" in blocks[0].table_body


def test_image_and_equation():
    md = "![图A](images/a.jpg)\n\n$$ E=mc^2 $$\n"
    blocks = parse_markdown(md)
    assert blocks[0].type == IMAGE and blocks[0].img_path == "images/a.jpg"
    assert blocks[1].type == EQUATION and blocks[1].latex == "E=mc^2"


def test_list():
    blocks = parse_markdown("- 甲\n- 乙\n")
    assert blocks[0].type == LIST_T
    assert blocks[0].items == ["甲", "乙"]


def test_html_img():
    blocks = parse_markdown('<div><img src="imgs/x.png" alt="章"/></div>')
    assert blocks[0].type == IMAGE and blocks[0].img_path == "imgs/x.png"
