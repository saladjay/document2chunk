from mineru2doc.model import TEXT, TABLE, IMAGE, EQUATION, Block
from mineru2doc.render import to_markdown


def test_heading_and_paragraph():
    blocks = [
        Block(type=TEXT, text="标题", level=2),
        Block(type=TEXT, text="正文内容"),
    ]
    md = to_markdown(blocks)
    assert "## 标题" in md
    assert "正文内容" in md


def test_page_numbers_cleaned():
    md = to_markdown([Block(type=TEXT, text="12"), Block(type=TEXT, text="12 / 34")])
    assert md.strip() == ""


def test_table_caption_and_body():
    md = to_markdown([Block(type=TABLE, table_body="<table></table>", caption="表1")])
    assert "表1" in md
    assert "<table></table>" in md


def test_image_and_equation():
    md = to_markdown([
        Block(type=IMAGE, img_path="images/a.jpg", caption="图1"),
        Block(type=EQUATION, latex="E=mc^2"),
    ])
    assert "![图1](images/a.jpg)" in md
    assert "$$ E=mc^2 $$" in md
